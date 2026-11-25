# Headless IceWM session

Runs Xvfb and IceWM as an existing non-root desktop user. Install IceWM and
its session dependencies before invoking this role, and choose the host through
the calling playbook's inventory. The display is local and X11 TCP is disabled.

Inputs are `icewm_headless_session_user` (required),
`icewm_headless_session_display` (default 1), and
`icewm_headless_session_screen` (default `1280x720x24`). The caller must ensure
that the display number is not already owned by another X server. Xauthority
restricts display access to the desktop user. Consumers of the display must run
as that user and set `XAUTHORITY=/run/xworkmate-x11/Xauthority`. This role enables two systemd
services. Check their status and the display through the desktop service after
application. A template change restarts the session and disconnects users.
To roll back, restore the previously saved unit files and reload systemd before
restarting the units. No desktop data is removed.
