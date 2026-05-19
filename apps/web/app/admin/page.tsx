"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { createApi } from "@/lib/api";
import type { OrgResponse } from "@/lib/api";
import { Button, Card, CardHeader, Field } from "@/components/ui";

export default function AdminPage() {
  const { currentUser, accessToken } = useAuth();

  if (currentUser && currentUser.organisation_type !== "altera_internal") {
    return (
      <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
        This page is only accessible to Altera internal users.
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Admin</h1>
        <p className="mt-1 text-sm text-gray-600">
          Create client organisations and invite users.
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
  const [inviteOrgId, setInviteOrgId] = useState<string | null>(null);

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

  return (
    <div className="space-y-6">
      <CreateOrgForm onCreated={loadOrgs} accessToken={accessToken} />

      <Card>
        <CardHeader title="Client organisations" />
        {loading ? (
          <p className="mt-3 text-sm text-gray-500">Loading…</p>
        ) : error ? (
          <p className="mt-3 text-sm text-rose-600">{error}</p>
        ) : orgs.length === 0 ? (
          <p className="mt-3 text-sm text-gray-500">No organisations yet.</p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
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
                  <td className="py-2 pr-4 font-mono text-xs text-gray-500">{org.slug}</td>
                  <td className="py-2 pr-4 text-gray-500">{org.organisation_type}</td>
                  <td className="py-2 text-right">
                    {org.organisation_type === "gms_client" && (
                      <button
                        onClick={() =>
                          setInviteOrgId(inviteOrgId === org.id ? null : org.id)
                        }
                        className="text-xs text-brand-600 hover:underline"
                      >
                        {inviteOrgId === org.id ? "Cancel" : "Invite user"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {inviteOrgId && (
        <InviteUserForm
          orgId={inviteOrgId}
          accessToken={accessToken}
          onDone={() => setInviteOrgId(null)}
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
          <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
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
// Invite user form
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
      <Card>
        <CardHeader title="Invite sent" />
        <div className="mt-3 space-y-2 text-sm">
          <p className="text-green-700">
            {result.invite_sent
              ? `Invite email sent to ${result.email}.`
              : `User pre-provisioned for ${result.email} (no email — dev mode).`}
          </p>
          <button onClick={onDone} className="text-xs text-brand-600 hover:underline">
            Done
          </button>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader title="Invite user" />
      <form onSubmit={handleSubmit} className="mt-4 space-y-4">
        <Field label="Email address">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="off"
            placeholder="user@client.com"
            className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </Field>
        <Field label="Role">
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="client_owner">Client Owner</option>
            <option value="client_admin">Client Admin</option>
            <option value="client_viewer">Client Viewer</option>
          </select>
        </Field>
        {error && (
          <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
            {error}
          </div>
        )}
        <div className="flex gap-2">
          <Button type="submit" disabled={busy}>
            {busy ? "Sending…" : "Send invite"}
          </Button>
          <button
            type="button"
            onClick={onDone}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      </form>
    </Card>
  );
}
