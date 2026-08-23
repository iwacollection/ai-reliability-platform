"use client";

export type ExecutionEvent = {
  type: string;
  message: string;
  timestamp: string;
};

export function ExecutionTimeline({events}:{events:ExecutionEvent[]}) {
  return (
    <div>
      <h2>Remediation Execution Timeline</h2>
      {events.map((event,index)=>(
        <div key={index}>
          <strong>{event.type}</strong>
          <span> {event.timestamp}</span>
          <p>{event.message}</p>
        </div>
      ))}
    </div>
  );
}
