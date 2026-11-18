-- SSH tunnel private keys are stored encrypted (like database passwords), never inside connections.config.
-- Any plaintext key that an earlier build placed in config is removed; its owner re-enters it.
ALTER TABLE connections ADD COLUMN ssh_key_ciphertext bytea;

ALTER TABLE connections NO FORCE ROW LEVEL SECURITY;
UPDATE connections SET config = config #- '{ssh_tunnel,ssh_private_key}' WHERE config -> 'ssh_tunnel' ? 'ssh_private_key';
ALTER TABLE connections FORCE ROW LEVEL SECURITY;

ALTER TABLE connections ADD CONSTRAINT connections_no_plaintext_ssh_key
  CHECK (NOT coalesce(config -> 'ssh_tunnel' ? 'ssh_private_key', false));
