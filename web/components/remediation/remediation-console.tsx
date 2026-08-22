export function RemediationConsole() {
  return (
    <section>
      <h2>Remediation Plan</h2>
      <p>Action: restart unhealthy pods</p>
      <p>Risk: medium</p>

      <h2>Human Approval</h2>
      <button>Approve</button>
      <button>Reject</button>

      <h2>Verification</h2>
      <p>Health check / Error rate / Latency</p>

      <h2>Rollback</h2>
      <button>Rollback</button>
    </section>
  );
}
