"use client";

import { ReactFlow, Background, Controls, Node, Edge } from "reactflow";
import "reactflow/dist/style.css";

interface Props {
  nodes: Node[];
  edges: Edge[];
}

export function EvidenceGraph({ nodes, edges }: Props) {
  return (
    <div className="h-[600px] rounded border bg-white">
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
