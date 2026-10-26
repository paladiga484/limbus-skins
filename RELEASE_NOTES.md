# limbus-skins 1.2.0

- **Greencliff** (`greencliff`): LCB Heathcliff, green from head to toe. A new kind of skin, a
  tint: the identity keeps its own drawings, recolored, so every frame stays exactly in place.
  His S3 is Gluttony, the green sin. And he carries the Black Forest: when he dies, every unit
  on both sides dies at the end of that round - built only from abilities the game already has.
- **Boss skills:** `limbus-skins boss on <skin>` gives a repaint its boss's skills too: the
  boss's power, coins and effects, played with the identity's own animations (already drawn as
  the boss), so a skill never asks for a motion the body lacks. Works for all 33 repaints of a
  boss; it picks the strongest version of the boss when the game has several. Off by default.
- **Vergilius** (`vergilius-wildhunt`): Wild Hunt Heathcliff as Vergilius on foot and as
  E.G.O. Vergilius in his mounted stance. With boss skills on he fights with Vergilius's own
  helper kit. A repaint can now draw each body of an identity from its own source.
- `preview` shows only the skin's own body, not every character sharing its bundle.
- Built skins are rebuilt automatically on the next `apply`.

Install: download the `.whl`, then `pip install --user limbus_skins-1.2.0-py3-none-any.whl`.
Check the download with `sha256sum -c SHA256SUMS`.

Private servers only. Boss skills and the Black Forest change how fights play; none of the new
skins has been seen in a fight yet, so try them on a spare account and report anything off.
