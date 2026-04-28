// PostCSS config. Tailwind + autoprefixer is the standard pairing —
// Tailwind generates utility classes from our markup; autoprefixer
// adds vendor prefixes for older browsers. The ``.cjs`` extension is
// load-bearing: this file lives outside the ESM scope of package.json
// so PostCSS's own loader picks it up correctly.
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
