"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { createApi } from "@/lib/api";
import type { MemberResponse, OrgResponse } from "@/lib/api";
import { Button, Card, CardHeader, Field } from "@/components/ui";

export default function AdminPage() {
  const { currentUser, accessToken } = useAuth();

  if (currentUser && currentUser.organisation_type !== "altera_internal") {
    return (
      <div className="rounded-md border border-danger-100 bg-danger-50 px-4 py-3 text-sm text-danger-700">
        This page is only accessible to Altera internal users.
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Admin</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Create client organisations and manage members.
        </p>
      </div>
      <OrgList accessToken={accessToken} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Organisation list + create form
// ---------------------------------------------------------------------------

function OrgList({ accessToken }: { accessToken: string | null }) {
  const api = createApi(accessToken);
  const [orgs, setOrgs] = useState<OrgResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);

  async function loadOrgs() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listOrgs();
      setOrgs(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadOrgs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  const selectedOrg = orgs.find((o) => o.id === selectedOrgId) ?? null;

  return (
    <div className="space-y-6">
      <CreateOrgForm onCreated={loadOrgs} accessToken={accessToken} />

      <Card>
        <CardHeader title="Client organisations" />
        {loading ? (
          <p className="mt-3 text-sm text-ink-soft">Loading…</p>
        ) : error ? (
          <p className="mt-3 text-sm text-rose-600">{error}</p>
        ) : orgs.length === 0 ? (
          <p className="mt-3 text-sm text-ink-soft">No organisations yet.</p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-left text-xs font-medium uppercase tracking-wider text-ink-soft">
                <th className="pb-2 pr-4">Name</th>
                <th className="pb-2 pr-4">Slug</th>
                <th className="pb-2 pr-4">Type</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {orgs.map((org) => (
                <tr key={org.id}>
                  <td className="py-2 pr-4 font-medium">{org.name}</td>
                  <td className="py-2 pr-4 font-mono text-xs text-ink-soft">{org.slug}</td>
                  <td className="py-2 pr-4 text-ink-soft">{org.organisation_type}</td>
                  <td className="py-2 text-right">
                    {org.organisation_type === "gms_client" && (
                      <button
                        onClick={() =>
                          setSelectedOrgId(selectedOrgId === org.id ? null : org.id)
                        }
                        className="text-xs text-brand-600 hover:underline"
                      >
                        {selectedOrgId === org.id ? "Close" : "Manage members"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {selectedOrg && (
        <OrgMembersPanel
          org={selectedOrg}
          accessToken={accessToken}
          onClose={() => setSelectedOrgId(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create org form
// ---------------------------------------------------------------------------

function CreateOrgForm({
  onCreated,
  accessToken,
}: {
  onCreated: () => void;
  accessToken: string | null;
}) {
  const api = createApi(accessToken);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  function deriveSlug(value: string) {
    return value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const org = await api.createOrg({ name, slug });
      setSuccess(`Organisation "${org.name}" created.`);
      setName("");
      setSlug("");
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Create client organisation" />
      <form onSubmit={handleSubmit} className="mt-4 space-y-4">
        <Field label="Organisation name">
          <input
            type="text"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setSlug(deriveSlug(e.target.value));
            }}
            required
            placeholder="Acme Retail Ltd"
            className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </Field>
        <Field label="Slug">
          <input
            type="text"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            required
            pattern="^[a-z0-9]+(-[a-z0-9]+)*$"
            placeholder="acme-retail"
            className="w-full rounded-md border border-gray-300 px-3 py-1.5 font-mono text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </Field>
        {error && (
          <div className="rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-xs text-danger-700">
            {error}
          </div>
        )}
        {success && (
          <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-800">
            {success}
          </div>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? "Creating…" : "Create organisation"}
        </Button>
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Members panel — list + invite + per-member actions
// ---------------------------------------------------------------------------

function OrgMembersPanel({
  org,
  accessToken,
  onClose,
}: {
  org: OrgResponse;
  accessToken: string | null;
  onClose: () => void;
}) {
  const api = createApi(accessToken);
  const [members, setMembers] = useState<MemberResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showInvite, setShowInvite] = useState(false);

  const loadMembers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listMembers(org.id);
      setMembers(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [org.id, accessToken]);

  useEffect(() => {
    loadMembers();
  }, [loadMembers]);

  return (
    <Card>
      <div className="flex items-start justify-between">
        <CardHeader title={`${org.name} — Members`} />
        <button onClick={onClose} className="text-xs text-gray-400 hover:text-ink-muted">
          Close
        </button>
      </div>

      {loading ? (
        <p className="mt-3 text-sm text-ink-soft">Loading members…</p>
      ) : error ? (
        <p className="mt-3 text-sm text-rose-600">{error}</p>
      ) : members.length === 0 ? (
        <p className="mt-3 text-sm text-ink-soft">No members yet.</p>
      ) : (
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 text-left text-xs font-medium uppercase tracking-wider text-ink-soft">
              <th className="pb-2 pr-4">Email</th>
              <th className="pb-2 pr-4">Name</th>
              <th className="pb-2 pr-4">Role</th>
              <th className="pb-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {members.map((m) => (
              <MemberRow
                key={m.user_id}
                member={m}
                orgId={org.id}
                accessToken={accessToken}
                onChanged={loadMembers}
              />
            ))}
          </tbody>
        </table>
      )}

      <div className="mt-4">
        {showInvite ? (
          <InviteUserForm
            orgId={org.id}
            accessToken={accessToken}
            onDone={() => {
              setShowInvite(false);
              loadMembers();
            }}
          />
        ) : (
          <button
            onClick={() => setShowInvite(true)}
            className="text-xs text-brand-600 hover:underline"
          >
            + Invite new user
          </button>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Individual member row — inline role change, resend invite, remove
// ---------------------------------------------------------------------------

const ROLE_LABELS: Record<string, string> = {
  client_owner: "Owner",
  client_admin: "Admin",
  client_viewer: "Viewer",
};

function MemberRow({
  member,
  orgId,
  accessToken,
  onChanged,
}: {
  member: MemberResponse;
  orgId: string;
  accessToken: string | null;
  onChanged: () => void;
}) {
  const api = createApi(accessToken);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  async function handleRoleChange(newRole: string) {
    if (newRole === member.role) return;
    setBusy(true);
    setFeedback(null);
    try {
      await api.updateMemberRole(orgId, member.user_id, { role: newRole });
      setFeedback("Role updated.");
      onChanged();
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleResend() {
    setBusy(true);
    setFeedback(null);
    try {
      const res = await api.resendInvite(orgId, member.user_id);
      setFeedback(res.invite_sent ? "Invite resent." : "Queued (dev mode).");
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove() {
    if (!confirm(`Remove ${member.email} from this organisation?`)) return;
    setBusy(true);
    setFeedback(null);
    try {
      await api.removeMember(orgId, member.user_id);
      onChanged();
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <tr>
      <td className="py-2 pr-4">{member.email}</td>
      <td className="py-2 pr-4 text-ink-soft">{member.display_name}</td>
      <td className="py-2 pr-4">
        <select
          value={member.role}
          onChange={(e) => handleRoleChange(e.target.value)}
          disabled={busy}
          className="rounded border border-gray-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-50"
        >
          {Object.entries(ROLE_LABELS).map(([val, label]) => (
            <option key={val} value={val}>
              {label}
            </option>
          ))}
        </select>
        {feedback && (
          <span className="ml-2 text-xs text-ink-soft">{feedback}</span>
        )}
      </td>
      <td className="py-2 text-right">
        <div className="flex justify-end gap-3">
          <button
            onClick={handleResend}
            disabled={busy}
            className="text-xs text-brand-600 hover:underline disabled:opacity-40"
          >
            Resend invite
          </button>
          <button
            onClick={handleRemove}
            disabled={busy}
            className="text-xs text-rose-600 hover:underline disabled:opacity-40"
          >
            Remove
          </button>
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Invite user form (inline inside members panel)
// ---------------------------------------------------------------------------

function InviteUserForm({
  orgId,
  accessToken,
  onDone,
}: {
  orgId: string;
  accessToken: string | null;
  onDone: () => void;
}) {
  const api = createApi(accessToken);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("client_owner");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ email: string; invite_sent: boolean } | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.inviteUser(orgId, { email, role });
      setResult({ email: res.email, invite_sent: res.invite_sent });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm">
        <p className="text-green-700">
          {result.invite_sent
            ? `Invite email sent to ${result.email}.`
            : `User pre-provisioned for ${result.email} (dev mode — no email sent).`}
        </p>
        <button onClick={onDone} className="mt-1 text-xs text-brand-600 hover:underline">
          Done
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-gray-200 bg-gray-50 p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wider text-ink-soft">
        Invite new user
      </p>
      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="flex gap-3">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="off"
            placeholder="user@client.com"
            className="flex-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="client_owner">Owner</option>
            <option value="client_admin">Admin</option>
            <option value="client_viewer">Viewer</option>
          </select>
          <Button type="submit" disabled={busy}>
            {busy ? "Sending…" : "Send invite"}
          </Button>
          <button
            type="button"
            onClick={onDone}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-ink-muted hover:bg-gray-100"
          >
            Cancel
          </button>
        </div>
        {error && (
          <div className="rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-xs text-danger-700">
            {error}
          </div>
        )}
      </form>
    </div>
  );
}
