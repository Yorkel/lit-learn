# New-module prompt for the Learning Notes app

Useful when you have a repo somewhere — a portfolio, a course you're taking, an assessment rubric — and you want Claude to read it, identify what concepts you need to learn, and turn that into a module (sections + topics) in your Learning Notes app.

**How to use:**
1. Open a Claude Code session inside the source repo (e.g. your AI Engineer portfolio).
2. Paste the **fenced block below** as your first message.
3. Claude reads the repo and replies with a single JSON object.
4. Save it as `/workspaces/lit-learn/templates/import_module_<slug>.json` in this codespace.
5. Run:
   ```bash
   python /workspaces/lit-learn/templates/import_module.py templates/import_module_<slug>.json
   ```
6. Reload the Learning Notes tab → new module appears with all sections/topics pre-filled.

---

## The prompt — copy from here to the end of the fence, paste into the source-repo Claude

```
I have a learning-notes app at github.com/yorkel/lit-learn that tracks what
I'm studying as a tree of:
  modules → sections → topics
where I tick off topics as I learn them and add notes per topic.

Read THIS repo carefully. It contains either (a) an assessment rubric / brief
I need to satisfy, (b) my existing portfolio of work so you can gap-analyse it
against the rubric, or (c) both. Use anything you can find: README, rubric.md,
assessment criteria, sample-of-work descriptions, marking schemes, recent work
artefacts.

Produce ONE JSON object that defines a learning module covering everything I
need to know / cover to reach DISTINCTION level on this assessment. Group
related concepts into sections, and break each section into 3-8 specific,
tickable topics.

OUTPUT FORMAT — emit only this JSON object, no surrounding prose:

{
  "module_title": "<concise module title, e.g. 'AI Engineer Distinction Prep'>",
  "sections": [
    {
      "title": "<section name, e.g. 'Evaluation & LLM-as-Judge'>",
      "topics": [
        {
          "name": "<specific tickable topic, e.g. 'Pairwise vs pointwise judge designs'>",
          "starter_notes": "<one or two sentences seeding the notes for this topic — what to learn, why it matters, what 'distinction-level' looks like. May be ''>",
          "resources": [
            {
              "title": "<paper title, video title, blog post>",
              "type": "<one of: video / paper / article / code / docs>",
              "authors": "<author or source name, e.g. 'Anthropic' or 'Bai et al.'>",
              "url": "<URL if known, else ''>"
            }
          ]
        }
      ]
    }
  ]
}

RULES:
- Be SPECIFIC in topic names — not "Understand prompting" but "Few-shot vs zero-shot prompt design tradeoffs".
- starter_notes is optional but useful — a 1-2 sentence seed of what the topic is about. Empty string is fine.
- resources is optional. Include 0-3 high-quality resources per topic if you know them; don't make up URLs.
- Aim for 4-8 sections, 3-8 topics per section. Not exhaustive — covering the highest-yield gaps.
- If the repo already has portfolio work that demonstrates a topic, you can flag that in starter_notes (e.g. "Already shown in src/eval/judge.py — focus on writing this up").
- Output the JSON object only — no markdown fence, no explanation.
```

---

## After you have the JSON

Save it under `templates/` and run:

```bash
python /workspaces/lit-learn/templates/import_module.py \
  /workspaces/lit-learn/templates/import_module_<slug>.json
```

The script **appends** the new module to your existing Learning Notes course (does not overwrite anything). Re-running with the same JSON replaces only that module by title.
