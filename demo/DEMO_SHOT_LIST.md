# Anti Radar: demo shot list (3:13)

This is the recording specification for the final narrated video. You can watch it at https://anti-radar-one.vercel.app/video or download it from the [v1.0.0 release](https://github.com/armaansingla14/Anti-Radar/releases/tag/v1.0.0).

- **Data:** the tour was recorded from `/demo` on the verified snapshot collected **20 Oct 2026**. This is the same data the public site serves (`DATA_SOURCE=snapshot`, no backend).
- **Format:** 1920×1080 at 30 fps, recorded by `e2e/record-demo.sh`.
- **Audio and captions:** narration from [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) was laid over the recording with `e2e/voiceover.py mix --tighten`, which also burns in the captions.
- **Timing:** times are positions in the final video.
- **Cursor:** in every scene it rests off-stage, bottom-left. The presenter drives each step with → and scrolls each page slowly.

## How to reproduce it

```sh
# frontend/ — snapshot build, no backend needed
DATA_SOURCE=snapshot npx next build && DATA_SOURCE=snapshot npx next start -p 3100

# e2e/
VO="uv run --no-project --with numpy --with imageio-ffmpeg python voiceover.py"
BASE_URL=http://localhost:3100 DEMO_DWELL="$($VO plan --audio memo.m4a)" ./record-demo.sh
$VO mix --audio memo.m4a --tighten
```

---

### 0:00–0:12 · Title
- **Page:** `/demo`, the title card.
- **Action:** none. Start is pressed as the scene ends.
- **Visible:**
  - the ANTI RADAR wordmark and "Conviction Before Consensus.";
  - "This isn't another startup database. Radar looks for technical change before a company becomes obvious.";
  - the header line "Anti Radar · Built for Anti Fund" with "Verified snapshot · as of 20 Oct 2026";
  - "Start →" and "Ready · ten scenes · about three minutes".
- **Narration:** "This is Anti Radar. It isn't another startup database. It looks for technical change before a company becomes obvious. Everything you'll see is real public data."

### 0:12–0:31 · The radar
- **Page:** `/`
- **Action:** slow scroll through What changed.
- **Visible:**
  - the masthead: "Built for Anti Fund · Verified snapshot · Tuesday 20 October 2026";
  - "Morning intelligence · Tuesday, October 20" and "VERIFIED SNAPSHOT · COLLECTED OCT 20, 2026";
  - the counts: **3** possible formations · **5** people to meet · **64** companies accelerating · **1** signal collision · **1** strange signal;
  - What changed:
    1. vLLM: ANTI SCORE +11 in 7 days (now 44)
    2. ShengShu: ANTI SCORE +9 in 7 days (now 47)
    3. Baidu gained 2,051 GitHub stars in 30 days without a single Hacker News thread
  - People to meet: Kevin Zakka, Zhengyi "Zen" Luo, Simon Mo, Simo Lin, Misha Goin.
- **Narration:** "This is the morning view for October 20th: what changed, ranked by evidence. Three possible formations are active right now, five people are worth meeting, and sixty-four companies are accelerating. Every row says why it surfaced now and how many sources back it."

### 0:31–0:56 · Formation Radar
- **Page:** `/formation`, then `/formation/person-sameh-khamis`.
- **Action:** list view, then open the top candidate.
- **Visible on the list:**
  - "Technical teams before the company announcement."
  - "Six possible formations in view: six early" (High 0 · Emerging 0 · Early 6);
  - top card: "Sameh Khamis — possible formation", Formation Score **44**, "previously NVIDIA (self-reported)" and "Strong technical trajectory · formation not yet verified".
- **Visible on the detail page:**
  - the Formation Score components:
    - Founder Quality ≈15/25
    - Team Convergence 0/25
    - Technical Activity ≈19/20
    - Formation Evidence ≈7/20
    - Novelty / Attention Gap ≈3/10
  - Why now: the bio mentions "founder" and "stealth" (20 Oct).
- **Narration:** "Formation Radar looks for people and teams before any announcement. There are six candidates today, and all of them are early. The top one is Sameh Khamis, at 44. His GitHub bio now reads 'Founder and CTO at Stealth Startup', and it lists NVIDIA, Google and Microsoft Research before that. That's self-reported, and it's labelled that way."

### 0:56–1:10 · Signal collision
- **Page:** `/formation/person-sameh-khamis`
- **Action:** scroll to Team convergence and the relationship graph.
- **Visible:**
  - strength **Low** · convergence **0** · prior ties **0** · families: GitHub;
  - "Single person — no team linked yet";
  - "Sameh Khamis — contributed to — bilawalsidhu/gods-eye-view", 766 commits, VERIFIED;
  - presenter caption: "1 independent signal, one source family (GitHub) — not yet a collision".
- **Narration:** "Here the radar holds back. It's one signal from one source, GitHub, and no team is linked yet. Convergence is zero. So it stays an early signal; it isn't called a company."

### 1:10–1:25 · Why now
- **Page:** `/formation/person-sameh-khamis`
- **Action:** scroll to the signal timeline.
- **Visible:**
  - 20 Oct: "Sameh Khamis's GitHub profile bio mentions 'stealth'" and "…mentions 'founder'";
  - the self-described bio "Founder & CTO at Stealth Startup. Ex-NVIDIA, Google, Microsoft Research.", marked INFERRED with a github.com source link;
  - gods-eye-view activity marked FACT.
- **Narration:** "The timeline gives the dated reason it surfaced: on October 20th his profile started saying founder and stealth. Every item links to its source and says whether it's a fact or an inference."

### 1:25–1:47 · The person
- **Page:** `/founders/kevin-zakka`
- **Action:** scroll the technical trajectory and the relationships.
- **Visible:**
  - frontier-lab contributions: 149 commits to google-deepmind/mujoco_menagerie and 123 to google-deepmind/mujoco, both VERIFIED;
  - repositories: mujoco (15.8k★), mujoco_menagerie (4,230★), kevinzakka/mjbatch (600★, created Sep 10);
  - 22 verified relationships;
  - presenter caption: "Kevin Zakka — 272 verified commits to frontier-lab repositories".
- **Narration:** "The second thread is Kevin Zakka: 272 verified commits to Google DeepMind's MuJoCo repositories. In September he started mjbatch, for running thousands of physics simulations in parallel, and it reached 600 stars in 40 days. That history comes from commit records, not from his bio."

### 1:47–2:13 · Who should we meet
- **Page:** `/meet`, then `/reports/f6a438de-8eab-48d5-a661-c32523a00597`.
- **Action:** open #1's meeting brief.
- **Visible on the Meet page:**
  - Week 43 · Oct 19–25, 2026; High 0 · Medium 5 · Watch 0;
  - the ranking caveat ("…people who work in the open are over-represented…");
  - #1 Kevin Zakka, MEDIUM PRIORITY:
    - affiliation unknown;
    - Founder score 48;
    - Possible formation 40;
    - Why this person / What changed / Why now with sources.
- **Visible in the brief:** "Meeting Brief — Kevin Zakka", "Anti Radar · Research brief", TEMPLATE, $0.00; 30-second context and Why on radar, each line cited and marked FACT or INFERENCE.
- **Narration:** "This is the weekly list, ranked by evidence rather than fame. Kevin is number one, at medium priority; nobody has higher priority this week. His meeting brief gives thirty-second context, what he's built, five questions and what to verify. Every line is cited and marked fact or inference. It's a template, because no language model is switched on."

### 2:13–2:38 · Attention gap
- **Page:** `/companies/shengshu`
- **Action:** scroll to ANTI SCORE and Attention gap.
- **Visible:**
  - ANTI SCORE **47** (↑9 this week);
  - attention gap: technical momentum **97** · public attention **26** · gap **+71**, flagged;
  - "How it's measured" note;
  - sources: Hacker News points p0 and GitHub stars p32; X and press marked unavailable;
  - timeline: shengshu-ai/Motus2 +616 stars in 30 days (44 → 660), star growth accelerating 7.8×, repository created Sep 11.
- **Narration:** "ShengShu has the biggest attention gap. Its new robotics model, Motus2, gained 616 GitHub stars in 30 days, and the last week ran almost eight times faster than the four before it. Technical momentum is 97; public attention is 26. There are no Hacker News threads, and X and press weren't measured, so they get no credit."

### 2:38–2:58 · Anti Brief
- **Page:** `/reports/a89e4ce9-2cc8-4a75-a2e1-752565e09141`
- **Action:** scroll the brief.
- **Visible:**
  - "Anti Brief — ShengShu", TEMPLATE, $0.00;
  - What they do: Vidu, MotuBrain; Motus2;
  - Why now: "3 signals in the past 39 days, all from GitHub … Single-source activity is weaker evidence than independent signals.";
  - What changed;
  - Founders: "Unknown — no evidence was collected for this section. Not a negative finding.";
  - numbered source citations.
- **Narration:** "Investigate turns that evidence into an Anti Brief: what they do, why now and what changed. Where the founders are unknown, it says so instead of guessing. Every claim has a source number, and it warns that single-source activity is weaker evidence."

### 2:58–3:13 · Close
- **Page:** `/demo`, the final card.
- **Action:** hold.
- **Visible:**
  - "CONVICTION BEFORE CONSENSUS.";
  - **6** formation candidates on the radar today · **5** people worth a first conversation this week · § every fact carries its public source;
  - "Explore the radar →".
- **Narration:** "Find the technical change early, show the evidence, and say what we don't know. Conviction before consensus."
