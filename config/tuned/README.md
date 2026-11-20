# Tuned limits per camera

`<camera id>.yaml` holds one camera's attention limits (up, side, notes/phone boundary), made by
`python -m worker.autotune <camera id>` from labelled `--tune` sessions. The worker reads it at
start-up and it replaces the `states:` defaults for that camera only. Delete the file to go back to
the defaults. `<camera id>.html` is the chart made with it, and `history/<camera id>/` keeps the last
five versions it replaced (copy one back to undo a tune).

These files are meant to be committed and shared. Only labelled tuning writes them; limits learned
during a lecture never do. See "Tuning the limits" in the top-level README.
