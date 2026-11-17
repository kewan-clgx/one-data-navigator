"use client";

import { useState } from "react";

type Draft = {
  bastion_host: string;
  bastion_port: string;
  bastion_user: string;
  ssh_private_key: string;
  known_hosts_fingerprint: string;
};

function SSHTunnelSetup({
  d,
  setD,
}: {
  d: Draft;
  setD: (updater: (prev: any) => any) => void;
}) {
  const [keyFile, setKeyFile] = useState<{ label: string; content: string } | null>(null);
  const [keyType, setKeyType] = useState<string | null>(null);

  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setD((v: any) => ({ ...v, [k]: e.target.value }));

  async function onKeyFile(file: File | undefined) {
    if (!file) {
      setKeyFile(null);
      setKeyType(null);
      return;
    }
    const text = await file.text();
    // Extract key type from PEM header: -----BEGIN <TYPE> PRIVATE KEY-----
    let type = "";
    const match = text.match(/-----BEGIN ([A-Z ]+) PRIVATE KEY-----/);
    if (match) {
      type = match[1].replace(/ /g, "-").toLowerCase();
    }
    setKeyFile({ label: `Loaded ${file.name}`, content: text });
    setKeyType(type || "ssh-rsa");
    // Update the form state with the loaded key
    setD((v: any) => ({ ...v, ssh_private_key: text }));
  }

  const displayedKey = keyFile?.content || d.ssh_private_key;

  return (
    <fieldset className="ssh-tunnel-setup">
      <legend>SSH tunnel configuration</legend>
      <p className="muted">Connect to private databases through a bastion host</p>

      <div className="settings-grid">
        <label>
          Bastion host
          <input required value={d.bastion_host} onChange={set("bastion_host")} placeholder="bastion.example.com" />
        </label>
        <label>
          Port
          <input required type="number" value={d.bastion_port} onChange={set("bastion_port")} min="1" max="65535" />
        </label>
        <label>
          SSH user
          <input required value={d.bastion_user} onChange={set("bastion_user")} placeholder="ubuntu or ec2-user" />
        </label>
      </div>

      <fieldset className="ssh-key-upload">
        <legend>SSH private key</legend>
        <div className="ssh-key-input-group">
          <input
            type="file"
            accept=".pem,.key,.ppk"
            aria-label="Upload SSH private key"
            onChange={(e) => onKeyFile(e.target.files?.[0])}
          />
          <div className="ssh-key-status">
            {keyFile && (
              <p className="ok" role="status">
                {keyFile.label} {keyType && <span className="ssh-key-type">{keyType}</span>}
              </p>
            )}
          </div>
        </div>

        <p className="muted">Or paste your private key:</p>
        <textarea
          rows={6}
          value={displayedKey}
          onChange={set("ssh_private_key")}
          placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n..."}
          aria-label="SSH private key PEM"
          spellCheck={false}
          autoComplete="off"
          className="mono"
        />
      </fieldset>

      <label>
        Server fingerprint (optional)
        <input
          value={d.known_hosts_fingerprint}
          onChange={set("known_hosts_fingerprint")}
          placeholder="SHA256:... or the server's public key"
        />
      </label>
    </fieldset>
  );
}

export default SSHTunnelSetup;
