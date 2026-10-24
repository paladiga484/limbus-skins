# limbus-skins 1.0.1

Fixes for repaints, and dependency floors with no known advisories.

- **Wrong character mid-fight:** some characters' files also hold a sidekick or summon
  (Lei Heng's holds another Thumb member), and a repaint could borrow their frames for
  some skills - Capo Meursault turned into someone else. Only the character's own
  animator lends frames now.
- **Unpainted motions:** reloads, parries and other identity-specific motions kept the
  identity's own drawings. They now take the other character's motion of the same name,
  or its first skill.
- `build` reports motions the other character has no drawing for (e.g. Callisto and
  Valencina have no dash pose, so their dashes use the idle pose).
- `limbus-skins off repaints` / `on helpers`: select a whole kind at once.
- Requires Pillow 13.0.0+ (12.x and older carry 40+ image-decoder advisories) and brotli
  1.2.1+ (UnityPy's; 1.2.0 has two). Older builds are rebuilt on the next `apply`.
- Copies in the game cache never write through a leftover or planted temp link.

Install: download the `.whl`, then `pip install --user limbus_skins-1.0.1-py3-none-any.whl`.
Check the download with `sha256sum -c SHA256SUMS`.

Seen working in a fight: the three tested helper skins. Repaints build and check out but
are still "not yet seen in a fight". Windows support is written but untested.
