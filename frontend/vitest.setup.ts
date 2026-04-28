// Vitest setup. ``@testing-library/jest-dom`` extends ``expect`` with
// DOM-aware matchers (``toBeInTheDocument`` etc.). Importing the
// matchers module is the side-effect that registers them; without
// this file every component test would have to import them by hand.
import '@testing-library/jest-dom/vitest';
