# Intelligent Schema Discovery Strategy

## Problem Statement
When users don't scope to a specific table in Explorer, the agent wastes 20-50 tool calls exploring all 51+ tables by calling `get_table_schema` on each one. This is wasteful and expensive.

## Solution: Smart Discovery with User Scoping

### Discovery Rule Chain (Hierarchical Fallback)

```
1. ACTIVE CONNECTION / UI SCOPE (Priority 1 — HIGHEST, exclusive)
   └─> If table is already selected in Explorer → USE IT
   └─> If only a connection is scoped → search ONLY within that connection
   └─> Never call list_connections or touch another connection while scoped
   
2. CONNECTION CHOICE (Priority 2, only when nothing is scoped)
   └─> Call list_connections exactly once
   └─> If only 1 connection exists → Focus on it
   └─> If multiple connections → Ask user which one (don't explore all), then stick to it
   
3. FAVORITES & RECENT (Priority 3)
   └─> Call get_my_tables
   └─> If answer found in favorites/recent → Done (don't explore further)
   
4. SEMANTIC TABLE SCOPING (Priority 4) [NEW]
   └─> Extract keywords from user query
   └─> Search for tables matching those keywords
   └─> Return TOP 3-5 related tables (not all 51)
   └─> Ask user: "I found these tables that might match your question. Which one?"
   └─> Only call get_table_schema on the user's chosen table
   
5. FULL EXPLORATION (Priority 5 - Fallback)
   └─> Only if user explicitly says "show me all tables"
   └─> Even then, page results (10 at a time)
```

### What the UI shares today (and the open gap)

The agent learns about the user's Explorer selection only through AG-UI context
(`useCopilotReadable` → `CONTEXT_STATE_KEY` → appended to the instruction under
"What the user currently has open in the app").

| Explorer state | Context the agent receives | Level 1 applies? |
|---|---|---|
| Table selected | `Scoped to table public.businesses in connection "devindulge_aws" (connection_id …)` | ✅ yes |
| Only a connection picked in the dropdown | `No table is selected in Explorer.` | ❌ no — agent falls to Level 2 |

**Observed (2026-11-17)**: with `devindulge_aws` picked but no table selected, the agent called
`list_connections`, then `get_my_tables` / `get_table_schema` / `search_tables_by_keyword` against the
BigQuery `idap-int` connection, and only reached `public.businesses` at q6. Commit 0b11adb made the
instructions enforce the scoped connection, but that only helps once the connection is actually in context.

**Fix needed**: lift the Explorer's selected `connectionId` to `AppShell` and share it via
`useCopilotReadable` even when no table is selected (changing the dropdown currently calls
`onScopeChange(null)` in `frontend/app/components/Explorer.tsx`).

### Implementation Details

#### 4a. Semantic Table Search Tool
Need a new tool: `search_tables_by_keyword(connection_id, keywords: str, limit=5)`
- Input: Keywords extracted from user query (e.g., "Las Vegas businesses status published")
- Returns: Top 5 matching tables ranked by relevance
- Uses: Table names + descriptions + stored keywords
- Output format:
  ```json
  {
    "tables": [
      {"path": "public.businesses", "relevance": 0.95, "description": "Business listings..."},
      {"path": "public.business_history", "relevance": 0.78, "description": "..."}
    ]
  }
  ```

#### 4b. Agent Decision Logic
If `search_tables_by_keyword` returns matches:
1. Present top results to user
2. Ask: "Which table should I query?"
3. Wait for user to pick one
4. Only then call `get_table_schema` on that table

If no matches:
- Suggest checking favorites/recent or manually selecting a table
- Offer: "Or tell me more about what you're looking for?"

### Examples

#### Example 1: Table Already Selected
```
User: "How many businesses in Las Vegas by status?"
UI Context: "Scoped to table public.businesses in connection devindulge_aws"
Agent Steps:
  1. See table is selected
  2. Call get_table_schema("public.businesses")
  3. Call run_sql with the query
Result: 2 tool calls ✅
```

#### Example 1b: Only a Connection Scoped (requires the UI fix above)
```
User: "How many businesses in Las Vegas and San Diego by status and published?"
UI Context: "Active connection devindulge_aws (postgres, connection_id b802…)"  ← not sent yet
Agent Steps:
  1. Connection is scoped → no list_connections, no BigQuery
  2. get_my_tables(b802…) → no hit
  3. search_tables_by_keyword(b802…, "business status published") → public.businesses
  4. get_table_schema(b802…, public.businesses)
  5. run_sql(b802…)
Result: 4 tool calls, all on the scoped connection ✅ (today: 6 queries, BigQuery detour ❌)
```

#### Example 2: Multiple Connections, No Selection
```
User: "Show me businesses by city"
UI Context: None (no table selected)
Connections: [bigquery "idap-int", postgres "devindulge_aws"]
Agent Steps:
  1. See multiple connections
  2. Ask user: "Which connection? BigQuery or PostgreSQL?"
  3. Wait for user response
Result: Prevents 51+ table exploration ✅
```

#### Example 3: Single Connection, Semantic Scoping
```
User: "Breakdown of businesses in Las Vegas by status and published"
UI Context: None selected
Connections: [postgres "devindulge_aws"] (only one)
Agent Steps:
  1. Single connection → focus on it
  2. Call get_my_tables → no favorites/recent match
  3. Call search_tables_by_keyword("Las Vegas businesses status published")
     Returns: [
       {path: "public.businesses", relevance: 0.95},
       {path: "public.business_listings", relevance: 0.72},
       {path: "public.business_audit_log", relevance: 0.45}
     ]
  4. Present to user: "Found 3 tables. Which one has business records?"
  5. User picks: "public.businesses"
  6. Call get_table_schema("public.businesses")
  7. Call run_sql
Result: 4 tool calls + 1 user clarification (not 23 schema scans!) ✅
```

#### Example 4: Favorites Hit
```
User: "Show top 5 businesses"
UI Context: None selected
Connections: [postgres "devindulge_aws"] (single)
Agent Steps:
  1. Single connection → focus on it
  2. Call get_my_tables → "businesses" in favorites
  3. Already found it! Get the schema and run query
Result: 2-3 tool calls (no semantic search needed) ✅
```

## Implementation Roadmap

### Phase 1: Immediate (Already Done)
✅ Update agent instructions to avoid multi-table exploration
✅ Prioritize UI-scoped table context
✅ Add "ask user" red flags
✅ Enforce scoped connection exclusively; red flags for cross-connection calls (commit 0b11adb)
🔲 Share the active connection (no table selected) from Explorer to the agent context

### Phase 2: Next Sprint (Recommended)
🔲 **Add `search_tables_by_keyword` tool** (backend/src/odn/agent/tools.py)
  - Use table catalog with keyword matching
  - Implement simple TF-IDF or semantic similarity
  - Rank by relevance score
  - Limit to top 5 results

🔲 **Update agent instructions** to use semantic scoping
  - Call search_tables_by_keyword when needed
  - Present top results to user
  - Ask for explicit choice before calling get_table_schema

🔲 **Test with real queries**
  - Single connection scenarios
  - Multiple connection scenarios  
  - Favorites/recent hits
  - Semantic search edge cases

### Phase 3: Future Enhancement
🔲 Persist user's favorite tables (already in my-tables)
🔲 Learn from query history which connections users use most
🔲 Auto-select connection if user has a clear primary workspace

## Metrics to Track

Before & After Comparison:
```
Metric                  Before    Target    Improvement
─────────────────────────────────────────────────────
Avg tool calls/query    23+       3-5       80-85%
Avg tokens/query        200-300k  10-20k    95%+
Avg cost/query          ~$0.10    ~$0.005   95%+
Time to first result    30-60s    5-10s     80%+
User satisfaction       Low       High      ++
```

## Fallback Strategies

**If semantic search fails:**
- Fall back to asking user directly
- Present a text input: "Which table or dataset should I search?"
- Then search for exact/fuzzy match

**If still no match:**
- Show first 10 tables from catalog
- Ask user to pick or describe what they want
- Offer: "Or upload your data as a table"

**If user says "show all":**
- Warn: "There are 50+ tables. This will cost tokens. Really want to explore all?"
- If yes: Page through results (10 at a time)
- For each page: Ask "Is the table you want here?"

## Code Changes Summary

### New Tool: `search_tables_by_keyword`
Location: `backend/src/odn/agent/tools.py`
```python
async def search_tables_by_keyword(connection_id: str, keywords: str, limit: int = 5) -> dict[str, Any]:
    """
    Search for tables by keyword/description relevance.
    
    Args:
        connection_id: Connection to search within
        keywords: Space-separated keywords from user query
        limit: Max results to return
    
    Returns:
        List of matching tables sorted by relevance score
    """
```

### Updated Instructions
Location: `backend/src/odn/agent/agent.py` (INSTRUCTION constant)
- Add: "Use `search_tables_by_keyword` to find relevant tables by keyword match"
- Add: "Present top 3-5 results to user, ask which one"
- Add: "Only call `get_table_schema` on user's chosen table"
- Keep: "If single connection + no favorites hit, use semantic search"

### Register Tool
Location: `backend/src/odn/agent/agent.py` (build_root_agent function)
```python
tools=[
    list_connections, get_my_tables, list_tables, 
    search_tables_by_keyword,  # NEW
    get_table_schema, run_sql, query_workspace,
    list_workspace, generate_chart,
]
```

## Why This Works

1. **Respects existing context**: Uses already-selected tables immediately
2. **Asks for clarity**: When ambiguous, asks user instead of guessing
3. **Semantic relevance**: Finds matching tables without full exploration
4. **Bounded exploration**: Top 5 results, not all 51
5. **User in control**: Explicit choices prevent wrong table selection
6. **Cost-effective**: 95%+ token reduction vs. exploration strategy
7. **Scalable**: Works with 10 tables or 1000 tables
