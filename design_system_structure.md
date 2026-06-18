# New Design System Structure

## Architecture Overview

```
static/css/
├── styles/                    # Core design foundation
│   ├── tokens.css             # Design tokens (colors, spacing, typography, shadows, z-index)
│   ├── globals.css            # CSS reset, base typography, scrollbar, containers
│   ├── animations.css         # All @keyframes, animation utility classes, skeletons
│   └── utilities.css          # Display, flex, gap, margin/padding, text, responsive helpers
│
├── components/                # Component-specific styles
│   ├── button.css             # Button variants (primary, ghost, soft, danger, card-action, toolbar)
│   ├── card.css               # Card, stat-card, college-card, feature-card, empty-state
│   ├── input.css              # Form inputs, selects, textareas, pill groups, checkboxes
│   ├── badge.css              # Badges, tags, pills, rank badges, chance badges
│   ├── navbar.css             # Navbar, user menu, dropdown, mobile menu
│   ├── table.css              # Tables, cutoff-table, compare-table
│   └── toast.css              # Toast notifications, alerts, flash messages
│
├── pages/                     # Page-specific layout styles
│   ├── layout.css             # Hero, grid, compare bar, map, premium sections, pagination
│   ├── admin.css              # Admin sidebar, topbar, dashboard stat icons
│   └── auth.css               # Auth pages (login/signup/reset), branding panel, forms
│
└── _backup/                   # Backup of all original CSS files
    ├── tokens.css.bak
    ├── design-tokens.css.bak
    ├── design-system.css.bak
    ├── globals.css.bak
    ├── base.css.bak
    ├── button.css.bak
    ├── components.css.bak
    ├── navbar.css.bak
    ├── layout.css.bak
    ├── auth.css.bak
    ├── utilities.css.bak
    └── animations.css.bak
```

## CSS Loading Order (All Templates)

All pages load CSS in the following order:
1. `styles/tokens.css` — Design tokens FIRST (variables must be defined)
2. `styles/globals.css` — Reset + base styles
3. `styles/animations.css` — Animation keyframes
4. `styles/utilities.css` — Utility classes
5. `components/button.css` — Button component
6. `components/card.css` — Card component  
7. `components/badge.css` — Badge component
8. `components/navbar.css` — Navbar component
9. `components/toast.css` — Toast component
10. `components/input.css` — Input component
11. `components/table.css` — Table component
12. `pages/layout.css` — Page layouts
13. `pages/auth.css` OR `pages/admin.css` — Page-specific styles

## Design Tokens Naming Convention

| Category | Pattern | Example |
|----------|---------|---------|
| Brand colors | `--color-primary*` | `--color-primary`, `--color-primary-hover` |
| Semantic colors | `--color-{name}*` | `--color-success`, `--color-error-bg` |
| Tier colors | `--color-{tier}*` | `--color-safe`, `--color-dream-border` |
| Spacing | `--space-{n}` | `--space-1` (4px), `--space-6` (24px) |
| Border radius | `--radius-{size}` | `--radius-sm`, `--radius-lg` |
| Shadows | `--shadow-{name}` | `--shadow-soft`, `--shadow-large` |
| Typography | `--text-{size}` / `--font-{weight}` | `--text-sm`, `--font-bold` |
| Transitions | `--duration-{name}` / `--ease-{name}` | `--duration-fast`, `--ease-out` |
| Z-index | `--z-{name}` | `--z-navbar`, `--z-modal` |

## Responsive Breakpoints

```css
/* Mobile:  ≤ 640px */   @media (max-width: 640px)
/* Tablet:  ≤ 768px */   @media (max-width: 768px)
/* Laptop:  ≤ 1024px */  @media (max-width: 1024px)
/* Desktop: > 1024px */  (default)