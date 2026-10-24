# limbus-skins 1.0.0

Dress Limbus Company identities as the characters they mirror, on **private servers only**.

- **45 skins**: 11 helper skins (the identity takes a helper unit's body *and* skills, e.g.
  LCB Ryōshū → Arayashiki Ryōshū from stage 9-49) and 34 repaints (the identity keeps its own
  body and skills but wears another character's drawings, e.g. Pequod Captain Ishmael →
  GasHarpoon Ahab), plus Solemn Lament Yi Sang with the Funeral of the Dead Butterflies' head
  (soft glow; in the idle its wings flutter and motes drift up).
- Repaints cover the Spine idle too, so the skin shows standing still, not only in motion;
  borrowed enemy drawings are mirrored to face the sinners' way.
- Built on your machine from your own installed game; no game art is downloaded or shipped.
  No network use at all.
- Swaps in about 0.2 s, keeps the game's file checksums, and puts every original back byte for
  byte when the game exits (`run`), or on `restore`.
- Rebuilds itself after game updates. `sheet` shows every skin's idle before and after;
  `art` puts your own picture on a head skin.

Install: download the `.whl`, then `pip install --user limbus_skins-1.0.0-py3-none-any.whl`.
Check the download with `sha256sum -c SHA256SUMS`. See the README for use.

Seen working in a fight: the three tested helper skins. Everything else builds and checks out
but has not been watched in a fight yet. Windows support is written but untested.
