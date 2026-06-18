# CSS Classes Removed/Consolidated During UI Cleanup

## Duplicate Style Systems Removed

1. **Old token naming** (`--brand`, `--safe`, `--danger`, `--border`, `--border-primary`) 
   → Unified to `--color-primary`, `--color-safe`, `--color-error`, `--border`

2. **Old button systems**: Button classes defined 3 times across `button.css`, `components.css`, `design-system.css`
   → Single source in `components/button.css`

3. **Old color systems**: 4 competing color/spacing/typography systems in `tokens.css`, `design-tokens.css`, `design-system.css`, `auth.css`
   → Unified `styles/tokens.css`

## Missing CSS Variable Added
- `--radius-circle: 50%` was missing but referenced in `components.css` and `navbar.css` — now added to tokens

## Dead/Unused CSS Removed
- `.btn-primary-lg` — never referenced in any template
- `.hover-lift` variant classes — consolidated to single version in animations.css
- Various `.ck-` prefixed classes from old navbar system — standardized to `.navbar-` prefix

## Duplicate CSS Classes Removed
- `.animate-fade-in`, `.animate-scale-in`, `.animate-pulse` — existed in both `design-system.css` and `animations.css`
- `.skeleton`, `.skeleton-text`, `.skeleton-card`, `.skeleton-avatar` — duplicated across animation files
- `.badge-safe`, `.badge-moderate`, `.badge-ambitious`, `.badge-dream` — defined in both `design-system.css` and `components.css`
- `.btn`, `.btn-primary`, `.btn-ghost`, `.btn-icon`, `.btn-sm`, `.btn-lg` — defined in 3+ locations
- Utility classes (`.d-flex`, `.gap-*`, `.mt-*`, `.mb-*`, `.text-*`, `.font-*`, `.rounded-*`) — 3 copies removed

## Legacy Code from design-system.css Extracted
The `design-system.css` contained broken CSS (line 722 unclosed `{` block, line 876 missing `}` for `.premium-recs-header`). These are now fixed in the new modular structure.