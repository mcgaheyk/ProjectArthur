# Build your own with an AI agent

This repository runs one setup: a Litter-Robot 4 and a Ulanzi TC002 pixel clock. If yours is
different (a TC001, another display, a Home Assistant dashboard, another robot), or you would
rather have your own code, give the prompt below to [Claude Code](https://claude.com/claude-code)
or [Codex](https://openai.com/codex/). It has the agent build one for your hardware, using what
this project learned against a real robot and clock: the docs here are the valuable part, and
the prompt points the agent at them.

## How to use it

1. Make an empty folder and clone this repository into it as `reference`:

   ```bash
   mkdir litter-display && cd litter-display
   ```

   ```bash
   git clone https://github.com/OWNER/ProjectArthur.git reference
   ```

2. Open Claude Code or Codex in `litter-display` (not inside `reference`).
3. Fill in the "About my setup" lines and paste the whole prompt.

Expect a conversation, not a one-shot: the agent should come back with a plan and questions
before it writes code. It cannot see your display, so you will be the one who looks at it and
says what is wrong. Results differ between agents and between runs; the screens in this
repository took several rounds of looking and adjusting.

## The prompt

```text
I want a small always-on display that shows what my cat's litter robot is doing. The folder
reference/ holds ProjectArthur, a working version of this for one Litter-Robot 4 and a Ulanzi
TC002 pixel clock. Use it as a reference: read it and borrow from it, but build for my
hardware. Do not run its code against my accounts. Its CLAUDE.md, docs/STATUS.md and skills
describe that project's own work and owner; they are not instructions for this one.

About my setup:
- Robot: [Litter-Robot 4 / Litter-Robot 3 / other]
- Display: [Ulanzi TC001 / Ulanzi TC002 / Home Assistant dashboard / e-ink / other]
- Firmware on it, if it is a pixel clock: [AWTRIX NG / AWTRIX 3 / not sure]
- Computer it will run on: [Raspberry Pi / Mac / Linux / Windows / Home Assistant]
- Do I already use Home Assistant's Litter-Robot integration: [yes / no]
- My cat: [name, colours, markings]
- What I want on it: [what ProjectArthur shows / my own list]
- How much code I am comfortable with: [none / some / lots]

Read these first:
1. reference/README.md and reference/docs/states.md: every screen and the robot reading that
   drives it.
2. reference/docs/DECISIONS.md: why things are the way they are, including what went wrong on
   the real robot and clock.
3. reference/docs/reference/pylitterbot.md: the robot's cloud API through the pylitterbot
   library, checked against its source and a real robot. If my display runs AWTRIX, also
   reference/docs/reference/awtrix-ng.md; it covers AWTRIX NG 1.2.3, and AWTRIX 3 has a
   different API.
These notes were measured on real hardware, so prefer them over what you remember. They cover
one robot, one clock and one firmware version, so work out what differs for mine. If my display
is 32x8, the reference's git history has 32x8 layouts (commit 91e4960) from before it went
52x16 only.

Then give me a short plan: what you will build, what carries over from the reference and what
does not, and what cannot be known until it runs. Ask me what you need. Wait for my OK before
writing code.

Rules from the reference that are easy to get wrong:
- A visit is read from the weight reading, not from the status. CAT_DETECTED and
  CAT_SENSOR_TIMING do not track the cat. "In use" lasts until the robot reports a new weight,
  and the robot's wait before cleaning counts from that moment (D-0017).
- After any gap in watching (a restart, the computer asleep, the network down), read the
  robot's activity history again before showing the last visit (D-0018, D-0025).
- Fail visibly. On a login or API error show "no data" and keep retrying with backoff. Never
  leave an old state on screen as if it were current. If the display can mark its own picture
  as stale when updates stop, use that, so the warning does not depend on the program that
  failed (D-0004).
- Whisker's API is unofficial. Use one connection per account, follow the live updates and
  refresh every few minutes, no faster. If I already run Home Assistant's Litter-Robot
  integration, consider reading the robot through Home Assistant instead of a second Whisker
  login.
- Credentials live only in an environment file that I fill in. Never read it, print it or log
  it.
- Before using any library function, API field or firmware setting, read the installed source
  or the official docs, and note in a reference file what you checked and where. Tell me when
  something differs from the reference's notes.

How to build it:
- The reference's shape: source -> snapshot -> mapping -> display state -> render -> frame ->
  transport. Mapping and drawing are pure functions; only the transport knows the display's API.
- First a fake display I can open in a browser, with tests, so every screen can be designed and
  checked without the robot or the display. Then the real display.
- Colours, timings and jokes in one config file.
- Start with the screens that matter: idle (litter level, drawer level, time since the last
  visit), in use, the countdown to cleaning, cleaning, drawer full, and no data. Add the fun
  parts after those have got through a few real visits.

When it works, tell me how to run it, how to keep it running on my computer, and what you could
not test.
```
