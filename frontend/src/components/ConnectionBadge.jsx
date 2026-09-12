const LABELS = {
  connecting: "Connecting…",
  open: "Live",
  closed: "Disconnected",
  error: "Error",
};

export default function ConnectionBadge({ status }) {
  return <span className={`badge badge-${status}`}>{LABELS[status] || status}</span>;
}
