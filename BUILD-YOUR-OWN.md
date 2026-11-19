# Set it up with an AI agent

The easiest way to get one of these: let [Claude Code](https://claude.com/claude-code) or
[Codex](https://openai.com/codex/) set it up. You answer its questions; it does the technical
parts and checks each step as it goes.

## Steps

1. Make an empty folder and open Claude Code or Codex in it.
2. Paste this and press Enter:

   ```text
   Help me set up ProjectArthur, a pixel clock that shows what my cat's litter robot is doing.
   Download it with: git clone https://github.com/mcgaheyk/ProjectArthur.git
   Then read ProjectArthur/docs/agent-setup.md and follow it. Ask me whatever you need as you go.
   If you can't download it, tell me.
   ```

3. Answer its questions.

## What it will ask

- Which litter robot you have, and which display, if any yet.
- Which computer will run it. It needs one that stays on; a laptop that sleeps freezes the
  clock until it wakes.
- What your cat looks like. A photo is the easiest answer.
- Your town, for the weather and the time on the clock (never your address), and whether you
  want °F or °C.

## What you do yourself

- Plug in the clock and click through the AWTRIX NG installer, if your clock does not run it
  yet. The agent walks you through it.
- Find the clock in your router's app or device list and read out its address.
- Type your Whisker app login into a file the agent opens for you. **Never paste your password
  into the chat.** The agent is told never to read that file.

## What to expect

- **With a Litter-Robot 4 and a Ulanzi TC002** (or no display yet), it sets up this project for
  you: your cat's colours and your town, a preview of every screen in your browser, your cat on
  the clock, and a way to keep it running.
- **With anything else** (a TC001, a Home Assistant dashboard, another display or robot), it
  builds one for your hardware using what this project learned. That is a bigger job: expect a
  plan and questions first, and a few rounds of looking at the display and saying what is
  wrong.
- Results differ between agents and between runs. The agent cannot see your display, so it
  will ask you what is on it.

## If the agent cannot download it

Some agents are not allowed on the network. Run this in your folder, then paste the prompt
again:

```bash
git clone https://github.com/mcgaheyk/ProjectArthur.git
```
