# Files Removed During UI Cleanup

## CSS Files Deleted (11 files)

| File | Size | Reason |
|------|------|--------|
| `static/css/design-tokens.css` | 165 lines | Duplicate of `tokens.css` (merged into `styles/tokens.css`) |
| `static/css/tokens.css` | 173 lines | Replaced by unified `styles/tokens.css` |
| `static/css/base.css` | 158 lines | Replaced by unified `styles/globals.css` |
| `static/css/globals.css` | 114 lines | Replaced by unified `styles/globals.css` |
| `static/css/design-system.css` | 1266 lines | Monolith split into modular component files |
| `static/css/button.css` | 180 lines | Moved into `components/button.css` |
| `static/css/components.css` | 614 lines | Split into `components/button.css`, `card.css`, `input.css`, `badge.css`, `table.css` |
| `static/css/navbar.css` | 211 lines | Moved into `components/navbar.css` |
| `static/css/layout.css` | 90 lines | Moved into `pages/layout.css` |
| `static/css/auth.css` | 1205 lines | Refactored into `pages/auth.css` |
| `static/css/utilities.css` | 120 lines | Moved into `styles/utilities.css` |
| `static/css/auth.js` (in CSS dir) | 627 lines | Duplicate of `static/js/auth.js` — deleted from wrong location |

## Total CSS removed: **~4,896 lines** of redundant/duplicate/conflicting code

## New Modular Files Created (11 files)

| File | Purpose |
|------|---------|
| `static/css/styles/tokens.css` | Single source of truth for all design tokens (colors, spacing, typography, shadows) |
| `static/css/styles/globals.css` | CSS reset, base typography, scrollbar, focus rings, container |
| `static/css/styles/animations.css` | All @keyframes + animation utility classes + skeletons |
| `static/css/styles/utilities.css` | Display, flex, spacing, text, color, responsive helpers |
| `static/css/components/button.css` | All button variants (primary, ghost, soft, danger, card actions, toolbar) |
| `static/css/components/card.css` | Card, stat-card, college-card, feature-card, empty-state |
| `static/css/components/input.css` | Form inputs, selects, textareas, pill groups, checkboxes |
| `static/css/components/badge.css` | Badges, tags, pills, rank badges, chance badges |
| `static/css/components/navbar.css` | Navbar, user menu, dropdown, mobile menu |
| `static/css/components/table.css` | Tables, cutoff-table, compare-table |
| `static/css/components/toast.css` | Toast notifications, alerts, flash messages |
| `static/css/pages/layout.css` | Page-level layouts (hero, grid, compare bar, map, premium sections) |
| `static/css/pages/admin.css` | Admin sidebar, topbar, dashboard styles |
| `static/css/pages/auth.css` | Auth pages (login/signup/reset), branding panel, form inputs, verification code |