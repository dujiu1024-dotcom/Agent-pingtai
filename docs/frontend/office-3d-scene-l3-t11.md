# Digital Office 3D scene and responsive fallback

Issue: #748 (L3-T11), building on the shared Office workspace implementation.

## Scene layout

The Office WebGL scene uses the repository's approved visual direction and a navigable three-floor shell. Each floor exposes six spatial zones:

- Department collaboration area
- Open workstations
- Private offices
- Meeting rooms
- Archive / knowledge center
- Operations center

Workstations are selectable and Agent markers are selectable. Floor, Agent status and task metadata are read from the Office state snapshot. A desk identifier alone is not treated as a coordinate: Agents without server-supplied position, x and z remain in the inspector and are not placed at an invented location.

## Level of detail

Three.js LOD via Drei Detailed selects high-, medium- and low-detail Agent and workstation geometry at increasing camera distances. Canvas device-pixel ratio is capped to reduce fill-rate and shadow cost. The scene retains semantic status colors and selection affordances at each LOD.

## Mobile / low-capability fallback

At viewport widths up to 760px or when a coarse pointer is detected, the WebGL canvas is replaced by a 2D Office view. It retains floor navigation, zone overview, selectable workstation cards and Agent status/task rows. A 2D view switch is also available on desktop. This fallback avoids requiring WebGL on mobile and supports narrow layouts without hiding Agent state.

## Data and verification boundaries

- Agent status is derived from the server snapshot / associated Run and unknown values fall back to a neutral offline presentation.
- A server desk ID is not sufficient to infer spatial coordinates.
- Current Office state is polled every 10 seconds; this change does not claim SSE/WebSocket push delivery or layout persistence.
- Physical-device verification is skipped as requested. CI is the authoritative build/test result.
