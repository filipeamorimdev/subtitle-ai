/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    borderRadius: {
      none: '0',
      sm: '4px',
      DEFAULT: '4px',
      md: '4px',
      lg: '4px',
      xl: '4px',
      '2xl': '4px',
      '3xl': '4px',
      full: '9999px',
    },
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['"IBM Plex Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      colors: {
        ink: {
          50: '#f4f7f7',
          100: '#e3ebea',
          200: '#c5d6d4',
          300: '#9bb8b5',
          400: '#6f948f',
          500: '#547974',
          600: '#42605c',
          700: '#384e4b',
          800: '#304140',
          900: '#2a3837',
          950: '#151f1e',
        },
        accent: {
          DEFAULT: '#c45c26',
          soft: '#e8a07a',
        },
      },
    },
  },
  plugins: [],
}
