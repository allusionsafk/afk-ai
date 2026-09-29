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

The shell is a single resizable window with a 980×650 logical minimum and a
1120×760 default, fitted to the available work area. A 228 px paper rail carries
the AFK mark, name, version and navigation. The current page has a quiet
selected surface and semibold label. The main surface follows the current task:

- setup keeps the prerequisite status together, followed by its message and
  one primary recovery action;
- daily home gives Open Chat the strongest placement, then service actions and
  diagnostic/support utilities; its work area stays bounded and balances within
  a wide window;
- Models & fit keeps a dense installed-model list, with selected-model evidence
  below it; Optimization separates AFK's recommendation, measured evidence,
  current setting, and user override;
- details expand inline rather than opening a modal, and Ready activity starts
  collapsed.

Spacing follows an 8 px base with 4, 8, 12, 16, 24, and 32 px steps. Grouping
surfaces have subtle borders and 10–12 px corner radii. Do not use colored side
stripes, nested cards, or repeated icon-heading-description tile grids.

## Components

### Status rows

A status row contains a fixed-width symbol, component name, bold state label,
and one short explanation. Rows share a surface instead of becoming individual
cards. Unknown, blocked, ready, and not-required states remain visually distinct
through symbol, label, and semantic color.

### Buttons

Primary buttons use ink fill and light text. Secondary buttons use a quiet
paper surface and a restrained border. Real WinForms buttons have modest
rounded corners and visible keyboard focus. Text links disclose details or
open support destinations.
Every button has default, hover, pressed, focus, disabled, and busy behavior.
Busy buttons retain their label with a concise changing verb such as
`Checking…` or `Starting…`.

### Progress

Use a Windows progress bar with adjacent stage text and a short current action.
Indeterminate progress is only for genuinely unbounded third-party setup.
Completed stages remain visible as a compact checklist. Progress never erases a
failure or the recovery action.

### Details and logs

Details expand inline into a sunken paper surface with selectable, scrollable
monospace machine text. Ordinary explanations remain in the system text face.
The default collapsed summary always contains the user-level message and stable
reason code. The Ready activity surface remains closed until requested.

### Application mark

The existing AFK A mark is used in the rail, title bar, executable, taskbar and
shortcuts. The executable embeds a multi-resolution icon with transparent edges;
the window uses the same icon resource.

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
