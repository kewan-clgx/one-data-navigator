-- Length-prefixed components preserve path boundaries, including dots in identifiers.
CREATE FUNCTION odn_metadata_path_key(parts text[]) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT coalesce(string_agg(char_length(part)::text || ':' || part, '' ORDER BY ord), '')
  FROM unnest(parts) WITH ORDINALITY AS p(part, ord)
$$;

ALTER TABLE metadata_fields
  ADD COLUMN table_path_key text GENERATED ALWAYS AS (odn_metadata_path_key(table_path)) STORED;
CREATE INDEX metadata_fields_path_key ON metadata_fields (connection_id, table_path_key, ordinal);
