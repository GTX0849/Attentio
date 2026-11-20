# Tuned limits per camera

`<camera id>.yaml` holds one camera's attention limits (up, side, notes/phone boundary), made by
`python -m worker.autotune <camera id>` from labelled `--tune` sessions. The worker reads it at
start-up and it replaces the `states:` defaults for that camera only. Delete the file to go back to
the defaults. `history/<camera id>/` keeps the last five versions it replaced (copy one back to undo a
tune).

The limits file and its history are small and safe to share, so they are meant to be committed. Only
labelled tuning writes them; limits learned during a lecture never do.

`<camera id>.html` is the chart made with the tune. It is local only and is not committed
(`*.html` here is git-ignored): it holds up to 6000 raw head-angle points from the tuning runs. Run
`python -m worker.autotune <camera id>` again to redraw it. See "Tuning the limits" in the top-level README.
