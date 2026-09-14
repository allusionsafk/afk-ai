# Product

## Register

product

## Users

AFK LocalAI is for Windows users who want a private, capable local AI workspace
without becoming Docker, WSL, PowerShell, Python, or model-serving experts.
Friend Beta users may be willing to report rough edges, but they should still be
able to download one installer, follow ordinary application prompts, and reach a
working chat experience without developer knowledge.

The primary workflow is installing and starting local AI safely. During first
run, the user needs to understand whether their PC is ready, what AFK LocalAI is
doing, and exactly one next action when Windows or a runtime blocks progress.
After setup, the main job is opening chat and checking or repairing local
services when necessary.

## Product Purpose

AFK LocalAI turns a complex local stack—Ollama, Docker Desktop, WSL, Open WebUI,
search, voice, and hardware-fit models—into normal Windows software. Success is
one trustworthy `.exe`, a calm resumable setup, a clear usable-state gate, and a
daily launcher that keeps terminals and implementation details out of the normal
experience while preserving honest diagnostics for support.

## Brand Personality

Calm, capable, candid.

The product should feel like a careful Windows utility made by someone who
expects real machines to be messy. It explains risk and recovery without alarm,
never overclaims privacy or compatibility, and earns confidence through precise
status, durable progress, and plain language.

## Anti-references

- A script bundle with console windows, stack traces, command snippets, or
  developer terminology as the primary interface.
- A gamer-style control panel with neon decoration, dense telemetry, or animated
  effects competing with the task.
- A generic SaaS onboarding wizard full of decorative cards, gradients, vague
  celebration, or hidden technical consequences.
- An installer that claims success before health checks pass, or suggests
  disabling Windows security to get past warnings.

## Design Principles

1. **One decision at a time.** Show the whole machine status, but make one next
   action unmistakable.
2. **Progress must survive reality.** Restarts, partial installs, closed windows,
   and corrupt checkpoints become recoverable states rather than dead ends.
3. **Technical truth, translated.** Keep precise codes and logs available while
   presenting the normal explanation in user language.
4. **Usable is a verified state.** Completion means the supported path passes
   health checks, not merely that files were copied.
5. **Advanced control stays opt-in.** Preserve existing configurations and avoid
   silently changing firmware, boot, Docker contexts, or user data.

## Accessibility & Inclusion

Target WCAG 2.2 AA-equivalent contrast and keyboard behavior for the native
desktop surface. All controls require visible focus, useful accessible names,
logical tab order, scalable text at Windows DPI settings, non-color status
labels, and no motion required to understand progress. Errors must be readable
without technical vocabulary and diagnostics must remain copyable for users who
need remote help.
