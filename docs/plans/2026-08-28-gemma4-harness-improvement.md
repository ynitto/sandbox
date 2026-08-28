いる。しかも 2026年に入って「弱いモデルを強いハーネスでエージェント化する」研究・OSSがかなり増えている。Gemma 4 E4B はまさに実験対象にされていて、あなたが考えている方向はかなり面白いところに来てる。

特に面白かったのが、Gemma4:E4B を実際に測っている Argus。小型モデル前提で、tight tool contracts、deterministic execution、verifier、memory、skills を組み合わせている。公開ベンチでは Gemma4:E4B が tool-chain の総合で 83% とされていて、Qwen2.5-3B の58%をかなり上回っている。もちろん独自ベンチなので数字そのものは割り引く必要があるけれど、「E4Bをハーネスで使う」という実例としてかなり近い。

Argus Agent GitHub⁠￼

さらに直球なのが SmallCTL。説明そのものが「small local/self-hosted language models向け agentic harness」で、staged task flow → evidence tracking → context compression → tool safety → recovery という構成になっている。

SmallCTL GitHub⁠￼

Gemma4:E4Bなら、こういうハーネスがかなり有望

僕なら「E4B自身を賢くしよう」とするより、モデルに判断させる範囲そのものを削る方向に振る。

User / Issue
      │
      ▼
┌──────────────────────┐
│ Deterministic Router │  ← LLMにやらせない
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Context Builder      │
│ repo / skill / RAG   │
└──────────┬───────────┘
           ▼
     Gemma4:E4B
       PLAN ONLY
           │
           ▼
┌──────────────────────┐
│ Plan Validator       │ ← JSON Schema / rules
└──────────┬───────────┘
           ▼
     Gemma4:E4B
     ONE STEP ONLY
           │
           ▼
┌──────────────────────┐
│ Tool Executor        │ ← deterministic
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Evidence Collector   │
│ test / diff / logs   │
└──────────┬───────────┘
           ▼
     Gemma4:E4B
       VERIFY
           │
       ┌───┴────┐
       │        │
     PASS     FAIL
       │        │
       │     Recovery
       │        │
       └────┬───┘
            ▼
        NEXT STEP

重要なのは ReActを自由に延々回させないこと。

実際、Gemma4:E4Bを含む小型モデルを調べた state-harness の実験では面白い現象が出ている。Gemma4:E4B は通常条件35%に対し、単純に余計なターンを打ち切る naive cap だけで70%になったと報告されている。つまり、途中までは正しいのに、その後自分で壊してしまうケースが相当ある。著者も小型モデルでは open-ended coding のターン数を2〜3程度に制限することを示唆している。

これはかなり重要だと思う。

「もっと考えさせる」ではなく
「一回に考えさせる量を減らす」

のがE4B級では効く可能性が高い。

⸻

さらに研究としてかなり近いものが出ている

2026年7月の Better Harnesses, Smaller Models はまさにこの問題を研究している。

小型モデルをそのまま大型モデル用ハーネスへ差し替えると弱い。しかし、失敗パターンに合わせてinstructions / tools / orchestration loopを適応させると、21組中16組で性能改善、7組ではSLMとLLMの差を埋め、最良ケースでは LLM性能の89.7%を4%のコストで回収したとしている。特に反復性・定型性のある仕事ほど効く。

さらに Microsoft Research もかなり本気でこの方向をやっている。

MagenticLite / MagenticBrain は「small models optimized harness」を明示していて、曖昧な依頼を具体的計画へ変換し、tool/subagent選択、coding、failure recoveryまでハーネス側との協調で行う設計。

Microsoft Research — MagenticLite / MagenticBrain⁠￼

そして Microsoft Agent Framework の2026年版 Harness 自体も、

planning → todo tracking → context compaction → file memory → tool approval → persistent session

をハーネスの基本機能としている。

⸻

なので、E4B用なら「7つの補助輪」にする

僕なら次を優先する。

1. Finite State Machine
    * PLAN → ACT → VERIFY → RECOVER
    * モデルに「次何する？」を自由回答させない。
2. One-step execution
    * 一度に1タスクだけ。
    * 長いTODOを一気に実行させない。
3. Schema constrained output
    * JSON Schema / grammar constrained decoding。
    * action, tool, args, expected_resultくらいまで固定する。
    * SLMのtool useでは特に有効というsurvey結果もある。
4. External verifier
    * 「できた？」をGemma自身の感想で判定しない。
    * test / compiler / lint / git diff / file existence / exit codeなどをoracleにする。
5. Evidence-based context
    * 「たぶんこのファイル」ではなく、
        ripgrep → relevant chunks → Gemma
        のように検索をハーネス側でやる。
6. Failure classifier + Recovery recipes
    * syntax error → compiler outputを渡してrepair
    * test failure → failing testだけ渡す
    * tool error → schemaを再提示
    * repeated action → stop/replan
        のように失敗ごとのルートを決める。
7. Hard stopping
    * 同じ操作2回
    * diffが改善しない
    * verifier連続失敗
    * token/context上限
        などで強制的にREPLANへ戻す。

これはSmallCTLの evidence/recovery/context compression や、agent-execution-harness の weak mode にかなり近い。後者も弱いモデル向けに「small steps / fewer files / typed evidence / repair hints」を明示している。

⸻

そして、あなたの用途ならもう一段面白い設計ができると思う。

Gemmaを「エージェント」にしない。

              ┌─────────────┐
              │ Harness OS  │
              └──────┬──────┘
                     │
       ┌─────────────┼──────────────┐
       ▼             ▼              ▼
 Repository       Planner       Verifier
  Analyzer         Gemma          Rules
       │             │              │
       └──────┬──────┴──────┬───────┘
              ▼             ▼
          Executor        Recovery
            Gemma          Engine
              │
              ▼
         deterministic
            tools

つまり エージェント本体 = state machine + tools + memory + verifier にして、Gemma4:E4Bはその中の「局所判断関数」に落とす。

これはあなたが以前考えていた

Repository Understanding → Backlog Planner → Dependency Graph → Task Scheduler → Parallel Implementation → Continuous Review → Validation

みたいな構造とかなり相性がいい。むしろE4Bでは、巨大な1エージェントに全部やらせるより、この各ステージを決定論的ハーネスで固定してGemmaを局所的に呼ぶ方が本命だと思う。

今回調べた中では、まず SmallCTL / Argus / state-harness / Microsoft MagenticLite の4つを読む価値が高い。特にSmallCTLはあなたの用途にかなり近い。

もし進めるなら次は、これら4つの実装をさらに掘って、「Gemma4:E4B専用・最強ハーネス」の具体的アーキテクチャ（コンポーネント、状態遷移、context budget、tool schema、retry戦略まで）に落としてみると面白いと思う。