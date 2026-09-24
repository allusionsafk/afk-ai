# AFK LocalAI Design System

## Foundation

AFK LocalAI is a warm workbench: an approachable, local, inspectable place to
work with a model on this PC. Paper-toned grounds, near-black ink and one ink
primary action; warmth comes from material and proportion, not from an accent
colour. It belongs to the Allusions family, whose frame is achromatic, and it
does not share DemiMedia's dark cinema or ValClips' kinetic look. Design serves
setup clarity; decoration never outranks status or action.

## Color

The Windows shell uses these sRGB values (`Theme.cs`). The web dashboard keeps its
existing tokens until it is converged separately.

| Role | Value | Use |
|---|---:|---|
| Background | `#F8F7F3` | Main window (paper) |
| Rail | `#EEECE6` | Navigation rail, with a `#D9D7CF` edge |
| Surface | `#FFFFFF` | Grouped content, with a `#DEDDD7` border |
| Elevated | `#F0EFE9` | Hover |
| Sunken | `#F5F3ED` | Logs and details |
| Border | `#C9C9C2` | Secondary button boundaries |
| Primary text | `#252622` | Headings and body |
| Secondary text | `#4E514B` | Supporting copy |
| Muted text | `#656862` | Metadata only |
| Action | `#292D2B` | Primary action (white text), hover `#464947` |
| Link | `#34465B` | Details and support links |
| Success | `#2F6844` on `#E5EFE5` | Ready/passing state |
| Warning | `#886028` on `#F6ECD9` | Recoverable attention state |
| Failure | `#8D4037` on `#FBF0EB` | Blocking/failure state |

Never communicate status with color alone. Each semantic color is paired with a
plain text state and a symbol (✓ ready, … working, ! attention, × blocked,
– not needed, ? unknown); `StatusPresentation` owns that translation and never
promotes a state. Headlines stay in ink whatever the state.

## Typography

Use one system family: `Segoe UI Variable` (Display cut for page titles, Text cut
for everything else), falling back to `Segoe UI`. Body text is 14 px at 100%
scaling, supporting metadata 12–13 px, section titles 16–18 px, and the page title
28–30 px. Use regular and semibold weights; avoid all-caps labels, wide tracking,
and fluid type. Machine values (model names, reason codes, logs) use Cascadia Mono.
Long explanations are limited to roughly 70 characters per line; engine messages
are reflowed so they wrap to the window rather than to a console.

## Layout

The shell is a single resizable window with a minimum logical size of 840×620.
A compact header carries product name, version/channel, and Help. The main
surface follows the current task:

- setup uses a two-column layout above 980 px: machine status on the left and
  action/progress on the right;
- narrower windows stack action/progress below status;
- daily home gives Open Chat the strongest placement, then service actions and
  diagnostic/support utilities;
- details expand inline at the bottom rather than opening a modal.

Spacing follows an 8 px base with 4, 8, 12, 16, 24, and 32 px steps. Group boxes
use full subtle borders and 10–12 px corner radii. Do not use colored side
stripes, nested cards, or repeated icon-heading-description tile grids.

## Components

### Status rows

A status row contains a fixed-width symbol, component name, bold state label,
and one short explanation. Rows share a surface instead of becoming individual
cards. Unknown, blocked, ready, and not-required states remain visually distinct
through symbol, label, and semantic color.

### Buttons

Primary buttons use cyan fill and dark ink. Secondary buttons use the elevated
surface and a full border. Text-link buttons are reserved for Support/About.
Every button has default, hover, pressed, focus, disabled, and busy behavior.
Busy buttons retain their label with a concise changing verb such as
`Checking…` or `Starting…`.

### Progress

Use a Windows progress bar with adjacent stage text and a short current action.
Indeterminate progress is only for genuinely unbounded third-party setup.
Completed stages remain visible as a compact checklist. Progress never erases a
failure or the recovery action.

### Details and logs

Details expand inline into a selectable monospace text area with Copy and Open
Log Folder actions. The default collapsed summary always contains the user-level
message and stable reason code.

### Empty and error states

An unchecked machine invites `Check this PC`; it never says merely “No data.” A
blocking state explains what is wrong, why it matters, and one next action.
Unknown evidence stops safely and offers Retry plus Diagnostics. Completion
offers `Open Chat` rather than celebration animation.

## Interaction and Motion

Use standard Windows controls and keyboard conventions. Enter activates the
primary safe action, Escape never cancels a running system mutation, and Alt+F4
asks before closing during a mutating phase. State transitions use only standard
100–200 ms hover/focus feedback; progress and meaning never depend on animation.

## Accessibility Verification

- Verify text and controls at 100%, 150%, and 200% Windows scaling.
- Verify keyboard-only navigation and visible focus across every action.
- Verify status remains understandable in grayscale and common color-vision
  deficiencies.
- Verify screen-reader names include action and target, not icon names.
- Keep logs selectable and copyable; never render actionable information only in
  transient notifications.
