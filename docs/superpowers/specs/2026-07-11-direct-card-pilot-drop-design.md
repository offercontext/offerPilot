# Direct Card-to-Pilot Drop Design

## Goal

Remove visible "add to Pilot" controls. A user drags a supported card itself; the drop target determines the result.

## Interaction

- Application, offer, and resume cards are direct draggable sources with no attachment button, label, or drag handle.
- Dropping a card on Pilot attaches its reference to the active Pilot conversation. Pilot visually indicates that it accepts the drop only while a compatible card is being dragged.
- On Kanban, dropping an application card on a status column preserves the existing status-transition behavior.
- On Kanban, dropping that same card on Pilot attaches the application reference and does not change its status.
- No drop (or an unsupported destination) leaves all data and the attachment draft unchanged.

## Accessibility

- The direct drag interaction must not introduce a visible attachment control.
- Keyboard users retain a non-visual-label alternative in each card's existing accessible action/menu surface. It must attach the same reference and use a descriptive accessible name.

## Architecture

- Non-Kanban cards use the current native drag payload contract, now attached to the card root rather than a dedicated handle.
- Kanban uses a single drag lifecycle with two target classes: its existing status columns and a Pilot target. The drop resolver dispatches exactly one outcome based on the final target.
- The Pilot attachment payload remains reference-only (`kind`, `id`, display label); server-side resolution and current conversation isolation are unchanged.

## Acceptance Criteria

1. No visible "添加到 Pilot" text, attachment button, or dedicated drag handle remains on supported cards.
2. Application, offer, and resume cards can be dragged directly into Pilot and appear as attachment chips.
3. A Kanban card dropped on a status column changes only status; the same card dropped on Pilot adds only an attachment.
4. Existing multi-panel conversation isolation and one-shot rail/drawer/page handoff remain intact.
5. Tests cover direct source drag, no visible attachment control, valid Pilot drop, and both Kanban destinations.
