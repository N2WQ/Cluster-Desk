# Code guide

Cluster Desk is a Tkinter desktop client. Its purpose is to let an operator
connect to CC Cluster and DXSpider nodes, inspect incoming spots, and manage
supported node interactions. The application is still a beta; a command being
queued is not proof that a cluster accepted it.

## Where to start

- `app.py` owns the windows, user choices, displayed data and the coordination
  of the main and additional node sessions. Tk widgets are updated on the Tk
  thread, where `poll` and `poll_secondary_nodes` consume connection events.
- `protocol.py` owns a connection worker, Telnet decoding, command validation
  and the conversion of supported incoming spot formats into `Spot` objects.
  The worker reports events; it does not directly update widgets.
- `filter_commands.py` turns supported filter choices into dialect-specific
  commands. `filter_readback.py` captures node responses, which must be checked
  to know what the node reports; display-only filters are a separate concern.
- `bands.py`, `cluster_dates.py` and the spot display logic group and format
  received records. The retained list is bounded and can include history and
  duplicates, so it is not a count of every spot on the node.
- `nodes.py` validates saved endpoints and callsigns. `settings_store.py`
  coordinates cooperating app instances and detects conflicting changes.
  External programs and older copies that ignore the lock can still overwrite
  the settings file.
- `spot_log.py` records spots, while `spot_archive.py` searches recordings;
  neither changes filters on the node. The other small modules separate
  features such as messages, reports, alarms, and country lookups.

## Follow a spot

1. `Connection.start` creates the socket worker in `protocol.py`. The worker
   logs in, handles Telnet negotiation, and emits events for the UI.
2. Supported records are parsed into `Spot`. Unrecognized lines remain node
   activity rather than being guessed into spots.
3. The appropriate UI poller consumes the event and hands spots to `add_spot`.
   Display filtering and `render_spots` decide what the operator sees.
4. When the user posts a spot, `open_spot` validates the selected session and
   `spot_command` constructs the command. Sending queues it; inspect the node's
   response in Activity to determine acceptance.

## Making changes

Preserve the distinction between node-side filter commands and local display
choices, and between a queued command and a node response. UI callbacks should
not directly manipulate a worker's socket. New parsers and command builders
should reject malformed or ambiguous input and have tests based on observable
behavior. Keep user settings, passwords, recordings and local reference files
out of commits. Tests that compare against local third-party reference files
need those files separately; see the README.
