# limbus-skins 1.1.0

- **Idle animations:** repainted units used to stand between moves as one still drawing that
  barely breathed. They now idle with the character's own animation, taken from that
  character in your game files and turned to face the sinners' way. 30 of the 34 repaints
  get it. Kromer, Lucio, Guido and the Old G Corp. Head Manager have no idle animation in
  the game, so they stay still.
- **Fix:** Nelly and the Pinky Nursefather each had a standing frame that could turn
  invisible.
- **Previews:** `preview` and `sheet` now pose characters the way the game does, so the
  idle GIF matches what you see in a fight.
- Built skins are rebuilt automatically on the next `apply`.

Install: download the `.whl`, then `pip install --user limbus_skins-1.1.0-py3-none-any.whl`.
Check the download with `sha256sum -c SHA256SUMS`.

Private servers only. Helper skins have been seen working in fights; repaints build and pass
every check, so report anything that looks off.
