"""Aggregate OpenRouter exchange-log usage and estimated cost for a job."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.ai_cost import estimate_cost_usd as _estimate_cost_usd


@dataclass(frozen=True)
class ModelPricing:
    name: str
    prompt_price_per_million: float | None
    completion_price_per_million: float | None


def classify_exchange(request: dict[str, Any] | None) -> str:
    """Infer the job-internal action kind from the chat request messages."""
    if not isinstance(request, dict):
        return "other"
    messages = request.get("messages") or []
    parts: list[str] = []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
    joined = "\n".join(parts).lower()
    if "classify media into a franchise universe" in joined:
        return "glossary_universe"
    if "extract audiovisual glossary terms" in joined:
        return "glossary_extract"
    if "translate only these" in joined and "missing subtitle blocks" in joined:
        return "repair"
    if "professional audiovisual subtitle translator" in joined:
        return "translate"
    return "other"


def _as_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def estimate_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    pricing: ModelPricing | None,
) -> float | None:
    if pricing is None:
        return None
    return _estimate_cost_usd(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_price_per_million=pricing.prompt_price_per_million,
        output_price_per_million=pricing.completion_price_per_million,
    )


def parse_exchanges(
    entries: list[dict[str, Any]],
    *,
    fallback_model: str,
    pricing_by_model: dict[str, ModelPricing],
) -> list[dict[str, Any]]:
    """Turn exchange-log entries into normalized usage rows.

    Counts every OpenRouter HTTP attempt: chat exchanges, batch submit/poll,
    and catalog fetches (when present in the log).
    """
    rows: list[dict[str, Any]] = []
    index = 0
    for entry in entries:
        event = entry.get("event")
        if event not in {"exchange", "batch_submit", "batch_poll", "batch_result", "catalog_list"}:
            continue
        request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
        response = entry.get("response") if isinstance(entry.get("response"), dict) else None
        body = response.get("body") if response and isinstance(response.get("body"), dict) else {}
        # Redacted logs store usage on response.usage; full logs on response.body.usage.
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        if not usage and response and isinstance(response.get("usage"), dict):
            usage = response["usage"]

        if event in {"exchange", "batch_result"}:
            action = classify_exchange(request)
            model = (
                (body.get("model") if isinstance(body.get("model"), str) else None)
                or (response.get("model") if response and isinstance(response.get("model"), str) else None)
                or (request.get("model") if isinstance(request.get("model"), str) else None)
                or fallback_model
            )
        elif event == "batch_submit":
            action = "batch_submit"
            model = (
                (request.get("model") if isinstance(request.get("model"), str) else None)
                or fallback_model
            )
        elif event == "batch_poll":
            action = "batch_poll"
            batch_id = request.get("batch_id") if isinstance(request.get("batch_id"), str) else "unknown"
            model = f"batch:{batch_id}"
        else:
            action = "catalog_list"
            model = "openrouter/catalog"

        input_tokens = _as_int(usage.get("prompt_tokens"))
        output_tokens = _as_int(usage.get("completion_tokens"))
        total_tokens = _as_int(usage.get("total_tokens")) or (input_tokens + output_tokens)

        reported_cost = _as_float(usage.get("cost"))
        pricing = pricing_by_model.get(model)
        estimated = estimate_cost_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            pricing=pricing,
        )
        if reported_cost is not None:
            cost_usd = reported_cost
            cost_estimated = False
        else:
            cost_usd = estimated
            cost_estimated = cost_usd is not None

        status_code = response.get("status_code") if response else None
        ok = (
            entry.get("error") is None
            and isinstance(status_code, int)
            and 200 <= status_code < 300
        )

        index += 1
        rows.append(
            {
                "index": index,
                "ts": entry.get("ts"),
                "model": model,
                "action": action,
                "attempt": entry.get("attempt") if isinstance(entry.get("attempt"), int) else None,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
                "cost_estimated": cost_estimated,
                "status_code": status_code if isinstance(status_code, int) else None,
                "ok": ok,
                "error": entry.get("error"),
            }
        )
    return rows


def aggregate_usage(exchanges: list[dict[str, Any]]) -> dict[str, Any]:
    requests = len(exchanges)
    input_tokens = sum(int(row["input_tokens"]) for row in exchanges)
    output_tokens = sum(int(row["output_tokens"]) for row in exchanges)
    total_tokens = sum(int(row["total_tokens"]) for row in exchanges)

    costs = [float(row["cost_usd"]) for row in exchanges if row.get("cost_usd") is not None]
    cost_usd = sum(costs) if costs else None
    if any(row.get("cost_usd") is None for row in exchanges) and exchanges:
        # Partial pricing: still surface summed known costs, but mark incomplete via pricing_source
        pass

    blended = None
    if cost_usd is not None and total_tokens > 0:
        blended = (cost_usd / total_tokens) * 1_000_000

    by_model: dict[str, dict[str, Any]] = {}
    by_action: dict[str, dict[str, Any]] = {}

    for row in exchanges:
        model = str(row["model"])
        action = str(row["action"])
        for bucket, key in ((by_model, model), (by_action, action)):
            item = bucket.setdefault(
                key,
                {
                    "key": key,
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "has_cost": False,
                },
            )
            item["requests"] += 1
            item["input_tokens"] += int(row["input_tokens"])
            item["output_tokens"] += int(row["output_tokens"])
            item["total_tokens"] += int(row["total_tokens"])
            if row.get("cost_usd") is not None:
                item["cost_usd"] += float(row["cost_usd"])
                item["has_cost"] = True

    def finalize(items: dict[str, dict[str, Any]], *, key_name: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in items.values():
            out.append(
                {
                    key_name: item["key"],
                    "requests": item["requests"],
                    "input_tokens": item["input_tokens"],
                    "output_tokens": item["output_tokens"],
                    "total_tokens": item["total_tokens"],
                    "cost_usd": item["cost_usd"] if item["has_cost"] else None,
                }
            )
        out.sort(key=lambda x: (-(x["cost_usd"] or 0), -x["total_tokens"], x[key_name]))
        return out

    pricing_flags = {
        "reported": any(not row.get("cost_estimated") and row.get("cost_usd") is not None for row in exchanges),
        "estimated": any(row.get("cost_estimated") for row in exchanges),
        "missing": any(row.get("cost_usd") is None for row in exchanges),
    }
    if pricing_flags["reported"] and pricing_flags["estimated"]:
        pricing_source = "mixed"
    elif pricing_flags["reported"]:
        pricing_source = "openrouter"
    elif pricing_flags["estimated"]:
        pricing_source = "estimated"
    else:
        pricing_source = "none"

    return {
        "requests": requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "blended_cost_per_million": blended,
        "pricing_source": pricing_source,
        "by_model": finalize(by_model, key_name="model"),
        "by_action": finalize(by_action, key_name="action"),
    }
