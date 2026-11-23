import { useRef, useState } from "@neko/plugin-ui";

export function useFileDrop(onFiles: (files: any[]) => void, disabled: boolean) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);
  const isFiles = (event: any) =>
    Array.from(event.dataTransfer?.types || []).includes("Files") ||
    !!event.dataTransfer?.files?.length;
  const capture = (event: any) => {
    if (!isFiles(event)) return false;
    event.preventDefault();
    event.stopPropagation();
    return true;
  };
  return {
    dragging: dragging && !disabled,
    handlers: {
      onDragEnter: (event: any) => {
        if (!capture(event)) return;
        depth.current += 1;
        if (!disabled) setDragging(true);
      },
      onDragOver: (event: any) => {
        if (!capture(event)) return;
        event.dataTransfer.dropEffect = disabled ? "none" : "copy";
      },
      onDragLeave: (event: any) => {
        if (!capture(event)) return;
        depth.current = Math.max(0, depth.current - 1);
        if (!depth.current) setDragging(false);
      },
      onDrop: (event: any) => {
        if (!capture(event)) return;
        depth.current = 0;
        setDragging(false);
        if (!disabled) onFiles(Array.from(event.dataTransfer.files || []));
      },
    },
  };
}
