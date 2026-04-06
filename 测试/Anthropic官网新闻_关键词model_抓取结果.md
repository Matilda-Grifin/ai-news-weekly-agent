# Anthropic News 抓取样例：关键词 *model* / 模型

- **列表页**：https://www.anthropic.com/news
- **方式**：Playwright 滚动列表 + 「See more」类交互；逐篇 Playwright 打开详情，BeautifulSoup 解析
- **列表阶段收集链接数**（目标上限 100）：**17**
- **详情扫描篇数**：**17**（上限 55，控制耗时）
- **命中关键词的条目数**：**14**（展示上限 30）

> **关键词规则**：英文 `model`（不区分大小写）；或中文「模型」。

---

## 命中条目

### 1. Introducing Claude Sonnet 4.6

- **链接**：https://www.anthropic.com/news/claude-sonnet-4-6
- **发布时间（页面解析）**：（未解析到）

**摘要**

Claude Sonnet 4.6 is a full upgrade of the model’s skills across coding, computer use, long-reasoning, agent planning, knowledge work, and design.

**正文片段**

Product Introducing Claude Sonnet 4.6 2026年2月17日 Claude Sonnet 4.6 is our most capable Sonnet model yet . It’s a full upgrade of the model’s skills across coding, computer use, long-context reasoning, agent planning, knowledge work, and design. Sonnet 4.6 also features a 1M token context window in beta. For those on our Free and Pro plans , Claude Sonnet 4.6 is now the default model in claude.ai and Claude Cowork . Pricing remains the same as Sonnet 4.5, starting at $3/$15 per million tokens. Sonnet 4.6 brings much-improved coding skills to more of our users. Improvements in consistency, instruction following, and more have made developers with early access prefer Sonnet 4.6 to its predecessor by a wide margin. They often even prefer it to our smartest model from November 2025, Claude Opus 4.5. Performance that would have previously required reaching for an Opus-class model—including on real-world, economically valuable office tasks —is now available with Sonnet 4.6. The model also shows a major improvement in computer use skills compared to prior Sonnet models. As with every new Claude model, we’ve run extensive safety evaluations of Sonnet 4.6, which overall showed it to be as...

---

### 2. Introducing Claude Opus 4.6

- **链接**：https://www.anthropic.com/news/claude-opus-4-6
- **发布时间（页面解析）**：Feb 5, 2026

**摘要**

We’re upgrading our smartest model. Across agentic coding, computer use, tool use, search, and finance, Opus 4.6 is an industry-leading model, often by wide margin.

**正文片段**

Announcements Introducing Claude Opus 4.6 Feb 5, 2026 We’re upgrading our smartest model. The new Claude Opus 4.6 improves on its predecessor’s coding skills. It plans more carefully, sustains agentic tasks for longer, can operate more reliably in larger codebases, and has better code review and debugging skills to catch its own mistakes. And, in a first for our Opus-class models, Opus 4.6 features a 1M token context window in beta 1 . Opus 4.6 can also apply its improved abilities to a range of everyday work tasks: running financial analyses, doing research, and using and creating documents, spreadsheets, and presentations. Within Cowork , where Claude can multitask autonomously, Opus 4.6 can put all these skills to work on your behalf. The model’s performance is state-of-the-art on several evaluations. For example, it achieves the highest score on the agentic coding evaluation Terminal-Bench 2.0 and leads all other frontier models on Humanity’s Last Exam , a complex multidisciplinary reasoning test. On GDPval-AA —an evaluation of performance on economically valuable knowledge work tasks in finance, legal, and other domains 2 —Opus 4.6 outperforms the industry’s next-best model...

---

### 3. Claude is a space to think

- **链接**：https://www.anthropic.com/news/claude-is-a-space-to-think
- **发布时间（页面解析）**：Feb 4, 2026

**摘要**

We’ve made a choice: Claude will remain ad-free. We explain why advertising incentives are incompatible with a genuinely helpful AI assistant, and how we plan to expand access without compromising user trust.

**正文片段**

Announcements Claude is a space to think Feb 4, 2026 There are many good places for advertising. A conversation with Claude is not one of them. Advertising drives competition, helps people discover new products, and allows services like email and social media to be offered for free. We’ve run our own ad campaigns , and our AI models have, in turn, helped many of our customers in the advertising industry. But including ads in conversations with Claude would be incompatible with what we want Claude to be: a genuinely helpful assistant for work and for deep thinking. We want Claude to act unambiguously in our users’ interests. So we’ve made a choice: Claude will remain ad-free. Our users won’t see “sponsored” links adjacent to their conversations with Claude; nor will Claude’s responses be influenced by advertisers or include third-party product placements our users did not ask for. The nature of AI conversations When people use search engines or social media, they’ve come to expect a mixture of organic and sponsored content. Filtering signal from noise is part of the interaction. Conversations with AI assistants are meaningfully different. The format is open-ended; users often sha...

---

### 4. Australian government and Anthropic sign MOU for AI safety and research

- **链接**：https://www.anthropic.com/news/australia-MOU
- **发布时间（页面解析）**：Mar 31, 2026

**摘要**

Anthropic is an AI safety and research company that's working to build reliable, interpretable, and steerable AI systems.

**正文片段**

Announcements Australian government and Anthropic sign MOU for AI safety and research Mar 31, 2026 Today, Anthropic signed a Memorandum of Understanding with the Australian government to cooperate on AI safety research and support the goals of Australia’s National AI Plan. Our CEO, Dario Amodei, met with Prime Minister Anthony Albanese to formalize the agreement during a visit to Canberra, Australia. We also announced AUD$3 million in partnerships with leading Australian research institutions to use Claude to improve disease diagnosis and treatment and support computer science education and research. Central to the MOU is a commitment to work with Australia’s AI Safety Institute. We will share our findings on emerging model capabilities and risks, participate in joint safety and security evaluations, and collaborate on research with Australian academic institutions. This mirrors the arrangements we have with safety institutes in the US, UK, and Japan, where early access and technical information sharing has helped governments build an independent view of where frontier AI is heading, and AI developers increase the safety of their models. Under the MOU, we will share Anthropic Ec...

---

### 5. Anthropic invests $100 million into the Claude Partner Network

- **链接**：https://www.anthropic.com/news/claude-partner-network
- **发布时间（页面解析）**：（未解析到）

**摘要**

We’re launching the Claude Partner Network, a program for partner organizations helping enterprises adopt Claude.

**正文片段**

Announcements Anthropic invests $100 million into the Claude Partner Network 2026年3月12日 We’re launching the Claude Partner Network, a program for partner organizations helping enterprises adopt Claude. We’re committing an initial $100 million to support our partners with training courses, dedicated technical support, and joint market development. Partners who join from today will get immediate access to a new technical certification and be eligible for investment. Anthropic is focused on ensuring that our AI model, Claude, serves the needs of businesses. To do this, we’ve partnered with a number of other companies. Notably, Claude is the only frontier AI model available on all three leading cloud providers: AWS, Google Cloud, and Microsoft. We also work with large management consultancies, professional services firms, specialist AI firms, and similar agencies. These organizations help our enterprise customers identify where Claude can provide the most value to their work, and then help them get started with our AI tools. Our partners act as trusted guides in what can feel like uncharted territory: navigating the deployment requirements, compliance, and change management necessar...

---

### 6. Introducing The Anthropic Institute

- **链接**：https://www.anthropic.com/news/the-anthropic-institute
- **发布时间（页面解析）**：（未解析到）

**摘要**

We’re launching The Anthropic Institute, a new effort to confront the most significant challenges that powerful AI will pose to our societies.

**正文片段**

Announcements Introducing The Anthropic Institute 2026年3月11日 We’re launching The Anthropic Institute , a new effort to confront the most significant challenges that powerful AI will pose to our societies. The Anthropic Institute will draw on research from across Anthropic to provide information that other researchers and the public can use during our transition to a world containing much more powerful AI systems. In the five years since Anthropic began, AI progress has moved incredibly quickly. It took us two years to release our first commercial model, and just three more to develop models that can discover severe cybersecurity vulnerabilities , take on a wide range of real work , and even begin to accelerate the pace of AI development itself . We predict that far more dramatic progress will follow in the next two years. One of our company’s core convictions is that AI development is accelerating: that the improvements we make are compounding over time. Because of this, extremely powerful AI, like the kind our CEO Dario Amodei describes in Machines of Loving Grace , is coming far sooner than many think. If this is right, society is shortly going to need to confront many massive...

---

### 7. Partnering with Mozilla to improve Firefox’s security

- **链接**：https://www.anthropic.com/news/mozilla-firefox-security
- **发布时间（页面解析）**：（未解析到）

**摘要**

Anthropic is an AI safety and research company that's working to build reliable, interpretable, and steerable AI systems.

**正文片段**

Policy Frontier Red Team Partnering with Mozilla to improve Firefox’s security 2026年3月6日 AI models can now independently identify high-severity vulnerabilities in complex software. As we recently documented, Claude found more than 500 zero-day vulnerabilities (security flaws that are unknown to the software’s maintainers) in well-tested open-source software. In this post, we share details of a collaboration with researchers at Mozilla in which Claude Opus 4.6 discovered 22 vulnerabilities over the course of two weeks. Of these, Mozilla assigned 14 as high-severity vulnerabilities —almost a fifth of all high-severity Firefox vulnerabilities that were remediated in 2025. In other words: AI is making it possible to detect severe security vulnerabilities at highly accelerated speeds. Firefox security vulnerabilities reported from all sources, by month. Claude Opus 4.6 found 22 vulnerabilities in February 2026, more than were reported in any single month in 2025. As part of this collaboration, Mozilla fielded a large number of reports from us, helped us understand what types of findings warranted submitting a bug report, and shipped fixes to hundreds of millions of users in Firefox 1...

---

### 8. Statement from Dario Amodei on our discussions with the Department of War

- **链接**：https://www.anthropic.com/news/statement-department-of-war
- **发布时间（页面解析）**：Feb 26, 2026

**摘要**

A statement from our CEO on national security uses of AI

**正文片段**

Announcements Policy Statement from Dario Amodei on our discussions with the Department of War Feb 26, 2026 I believe deeply in the existential importance of using AI to defend the United States and other democracies, and to defeat our autocratic adversaries. Anthropic has therefore worked proactively to deploy our models to the Department of War and the intelligence community. We were the first frontier AI company to deploy our models in the US government’s classified networks, the first to deploy them at the National Laboratories , and the first to provide custom models for national security customers. Claude is extensively deployed across the Department of War and other national security agencies for mission-critical applications, such as intelligence analysis, modeling and simulation, operational planning, cyber operations, and more. Anthropic has also acted to defend America’s lead in AI, even when it is against the company’s short-term interest. We chose to forgo several hundred million dollars in revenue to cut off the use of Claude by firms linked to the Chinese Communist Party (some of whom have been designated by the Department of War as Chinese Military Companies), sh...

---

### 9. Anthropic acquires Vercept to advance Claude's computer use capabilities

- **链接**：https://www.anthropic.com/news/acquires-vercept
- **发布时间（页面解析）**：Feb 25, 2026

**摘要**

Anthropic is an AI safety and research company that's working to build reliable, interpretable, and steerable AI systems.

**正文片段**

Announcements Anthropic acquires Vercept to advance Claude's computer use capabilities Feb 25, 2026 People are using Claude for increasingly complex work—writing and running code across entire repositories, synthesizing research from dozens of sources, and managing workflows that span multiple tools and teams. Computer use enables Claude to do all of that inside live applications, the way a person at a keyboard would. That means Claude can take on multi-step tasks in live applications, and solve problems impossible with code alone. Today, we're announcing that Anthropic has acquired Vercept to help us push those capabilities further. Vercept was built around a clear thesis: making AI genuinely useful for completing complex tasks requires solving hard perception and interaction problems. The Vercept team—including co-founders Kiana Ehsani, Luca Weihs, and Ross Girshick—have spent years thinking carefully about how AI systems can see and act within the same software humans use every day. That expertise maps directly onto some of the hardest problems we're working on at Anthropic. Vercept will wind down its external product in the coming weeks and join Anthropic in pushing the fron...

---

### 10. Anthropic’s Responsible Scaling Policy: Version 3.0

- **链接**：https://www.anthropic.com/news/responsible-scaling-policy-v3
- **发布时间（页面解析）**：Feb 24, 2026

**摘要**

An update to Anthropic's policy to mitigate catastrophic risks from AI

**正文片段**

Policy Announcements Anthropic’s Responsible Scaling Policy: Version 3.0 Feb 24, 2026 Read the Responsible Scaling Policy We’re releasing the third version of our Responsible Scaling Policy (RSP), the voluntary framework we use to mitigate catastrophic risks from AI systems. Anthropic has now had an RSP for more than two years, and we’ve learned a great deal about its benefits and its shortcomings. We’re therefore updating the policy to reinforce what has worked well to date, improve the policy where necessary, and implement new measures to increase the transparency and accountability of our decision-making. You can read the new RSP in full here . In this post, we’ll discuss some of the thinking behind the changes. The original RSP and our theory of change The RSP is our attempt to solve the problem of how to address AI risks that are not present at the time the policy is written, but which could emerge rapidly as a result of an exponentially advancing technology. When we wrote the original RSP in September 2023, large language models were essentially chat interfaces. Today they can browse the web, write and run code, use computers, and take autonomous, multi-step actions. As ea...

---

### 11. Announcing our updated Responsible Scaling Policy

- **链接**：https://www.anthropic.com/news/announcing-our-updated-responsible-scaling-policy
- **发布时间（页面解析）**：Oct 15, 2024

**摘要**

Today we are publishing a significant update to our Responsible Scaling Policy (RSP), the risk governance framework we use to mitigate potential catastrophic risks from frontier AI systems.

**正文片段**

Announcements Announcing our updated Responsible Scaling Policy Oct 15, 2024 Read the Responsible Scaling Policy Today we are publishing a significant update to our Responsible Scaling Policy (RSP), the risk governance framework we use to mitigate potential catastrophic risks from frontier AI systems. This update introduces a more flexible and nuanced approach to assessing and managing AI risks while maintaining our commitment not to train or deploy models unless we have implemented adequate safeguards. Key improvements include new capability thresholds to indicate when we will upgrade our safeguards, refined processes for evaluating model capabilities and the adequacy of our safeguards (inspired by safety case methodologies ), and new measures for internal governance and external input. By learning from our implementation experiences and drawing on risk management practices used in other high-consequence industries, we aim to better prepare for the rapid pace of AI advancement. The promise and challenge of advanced AI As frontier AI models advance, they have the potential to bring about transformative benefits for our society and economy. AI could accelerate scientific discover...

---

### 12. Claude Opus 4.6

- **链接**：https://www.anthropic.com/claude/opus
- **发布时间（页面解析）**：（未解析到）

**摘要**

Hybrid reasoning model that pushes the frontier for coding and AI agents, featuring a 1M context window

**正文片段**

Claude Opus 4.6 Hybrid reasoning model that pushes the frontier for coding and AI agents, featuring a 1M context window Try Claude Get API access Announcements NEW Claude Opus 4.6 2026年2月5日 Claude Opus 4.6 is our most capable model to date. Building on the intelligence of Opus 4.5, it brings new levels of reliability and precision to coding, agents, and enterprise workflows. Read more Claude Opus 4.5 2025年11月24日 Claude Opus 4.5 is our most intelligent model to date. It sets a new standard across coding, agents, computer use, and enterprise workflows. Opus 4.5 is a meaningful step forward in what AI systems can do. Read more Claude Opus 4.1 2025年8月5日 Claude Opus 4.1 is a drop-in replacement for Opus 4 that delivers superior performance and precision for real-world coding and agentic tasks. It handles complex, multi-step problems with more rigor and attention to detail. Read more Claude Opus 4 2025年5月22日 Claude Opus 4 pushes the frontier in coding, agentic search, and creative writing. We’ve also made it possible to run Claude Code in the background, enabling developers to assign long-running coding tasks for Opus to handle independently. Read more Availability and pricing For bus...

---

### 13. Claude Sonnet 4.6

- **链接**：https://www.anthropic.com/claude/sonnet
- **发布时间（页面解析）**：（未解析到）

**摘要**

Hybrid reasoning model with superior intelligence for agents, featuring a 1M context window

**正文片段**

Claude Sonnet 4.6 Hybrid reasoning model with superior intelligence for agents, featuring a 1M context window Try Claude Get API access Announcements NEW Claude Sonnet 4.6 2026年2月17日 Sonnet 4.6 delivers frontier performance across coding, agents, and professional work at scale. It can compress multi-day coding projects into hours and deliver production-ready solutions. Read more Claude Sonnet 4.5 2025年9月29日 Sonnet 4.5 is the best model in the world for agents, coding, and computer use. It’s also our most accurate and detailed model for long-running tasks, with enhanced domain knowledge in coding, finance, and cybersecurity. Read more Claude Sonnet 4 2025年5月22日 Sonnet 4 improves on Sonnet 3.7 across a variety of areas, especially coding. It offers frontier performance that’s practical for most AI use cases, including user-facing AI assistants and high-volume tasks. Read more Claude Sonnet 3.7 and Claude Code 2025年2月24日 Sonnet 3.7 is the first hybrid reasoning model and our most intelligent model to date. It’s state-of-the art for coding and delivers significant improvements in content generation, data analysis, and planning. Read more Availability and pricing Anyone can chat with...

---

### 14. Claude 4.5 Haiku

- **链接**：https://www.anthropic.com/claude/haiku
- **发布时间（页面解析）**：（未解析到）

**摘要**

Anthropic is an AI safety and research company that's working to build reliable, interpretable, and steerable AI systems.

**正文片段**

Claude 4.5 Haiku Our fastest model, a lightweight version of our most powerful AI, at a more affordable price Try Claude Get API access Announcements New Claude Haiku 4.5 2025年10月15日 Claude Haiku 4.5 is our fastest, most cost-efficient model, matching Sonnet 4’s performance on coding, computer use, and agent tasks. Claude Haiku 4.5 scores 73.3% on SWE-bench Verified, making it one of the world's best coding models. Read more Claude 3.5 Haiku 2024年10月22日 For a similar speed to Haiku 3, Haiku 3.5 improved across every skill set and surpassed Opus 3, the largest model in our previous generation, on many intelligence benchmarks. Read more Availability and pricing Anyone can chat with Claude using Haiku 4.5 on Claude.ai , available on web, iOS, and Android. For developers, Haiku 4.5 is available on the Claude Platform natively, and in Amazon Bedrock, Google Cloud's Vertex AI, and Microsoft Foundry. Claude Haiku 4.5 is also available in Claude Code. Pricing for Haiku 4.5 on the Claude Platform starts at $1 per million input tokens and $5 per million output tokens, with up to 90% cost savings with prompt caching and 50% cost savings with batch processing . To get started, simply use cl...

---
