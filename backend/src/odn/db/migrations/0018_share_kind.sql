-- "Just the answer" shares: one question, its final answer and the chosen result table/chart.
ALTER TABLE shared_conversations
  ADD COLUMN kind text NOT NULL DEFAULT 'conversation' CHECK (kind IN ('conversation', 'answer'));
