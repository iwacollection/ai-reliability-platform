type Props = {
  events: Array<{
    type: string;
    timestamp: string;
    payload: Record<string, unknown>;
  }>;
};

export function StreamEventPanel({ events }: Props) {
  return (
    <div>
      <h3>Live Agent Events</h3>
      {events.map((event, index) => (
        <div key={index}>
          <strong>{event.type}</strong>
          <span> {event.timestamp}</span>
          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
        </div>
      ))}
    </div>
  );
}
