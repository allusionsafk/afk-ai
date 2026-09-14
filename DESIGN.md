# AFK LocalAI Design System

## Foundation

AFK LocalAI uses the established Control Center identity: a restrained dark
desktop utility with cool neutral surfaces and one cyan action color. The native
shell should feel at home beside Windows 11 settings without imitating system
chrome. Design serves setup clarity; decoration never outranks status or action.

## Color

Use these sRGB equivalents in the Windows shell and retain the existing OKLCH
tokens in the web dashboard:

| Role | Value | Use |
|---|---:|---|
| Background | `#17191D` | Main window |
| Surface | `#202329` | Grouped content and details |
| Elevated surface | `#292D34` | Active/hover states |
| Border | `#3B4049` | Full control/group boundaries |
| Primary text | `#F1F3F5` | Headings and body |
| Secondary text | `#B8BEC8` | Supporting copy |
| Muted text | `#939AA6` | Metadata only |
| Accent | `#38BDF8` | Primary action, focus, selection |
| Accent ink | `#10212A` | Text on accent |
| Success | `#55C98D` | Ready/passing state |
| Warning | `#E5B85A` | Recoverable attention state |
| Failure | `#E2746B` | Blocking/failure state |

Never communicate status with color alone. Each semantic color is paired with a
plain text state and a familiar symbol. Accent appears on the primary action,
current step, focus ring, and progress only.

## Typography

Use one system family: `Segoe UI Variable`, falling back to `Segoe UI`. Body text
is 14 px at 100% scaling, supporting metadata 12–13 px, section titles 16–18 px,
and the page title 26–30 px. Use regular and semibold weights; avoid display
fonts, all-caps labels, wide tracking, and fluid type. Long explanations are
limited to roughly 70 characters per line.

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
