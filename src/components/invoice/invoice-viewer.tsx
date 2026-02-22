import { useState, useCallback } from "react";
import { ZoomIn, ZoomOut, RotateCw, Maximize2, Minimize2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

interface InvoiceViewerProps {
  imageUrl: string | undefined;
  isLoading: boolean;
  className?: string;
}

export function InvoiceViewer({
  imageUrl,
  isLoading,
  className,
}: InvoiceViewerProps) {
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const zoomIn = useCallback(
    () => setZoom((z) => Math.min(z + 0.25, 3)),
    [],
  );
  const zoomOut = useCallback(
    () => setZoom((z) => Math.max(z - 0.25, 0.25)),
    [],
  );
  const rotate = useCallback(
    () => setRotation((r) => (r + 90) % 360),
    [],
  );
  const toggleFullscreen = useCallback(
    () => setIsFullscreen((f) => !f),
    [],
  );
  const resetView = useCallback(() => {
    setZoom(1);
    setRotation(0);
  }, []);

  if (isLoading) {
    return (
      <div
        className={cn(
          "flex items-center justify-center bg-muted rounded-lg min-h-[400px]",
          className,
        )}
      >
        <Spinner size="lg" />
      </div>
    );
  }

  if (!imageUrl) {
    return (
      <div
        className={cn(
          "flex items-center justify-center bg-muted rounded-lg min-h-[400px] text-muted-foreground",
          className,
        )}
      >
        No image available
      </div>
    );
  }

  return (
    <div
      className={cn(
        "relative flex flex-col rounded-lg border bg-card",
        isFullscreen && "fixed inset-0 z-50 rounded-none",
        className,
      )}
    >
      {/* Toolbar */}
      <div className="flex items-center gap-1 border-b px-3 py-2">
        <Button variant="ghost" size="icon" onClick={zoomIn} title="Zoom in">
          <ZoomIn className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" onClick={zoomOut} title="Zoom out">
          <ZoomOut className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" onClick={rotate} title="Rotate">
          <RotateCw className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleFullscreen}
          title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
        >
          {isFullscreen ? (
            <Minimize2 className="h-4 w-4" />
          ) : (
            <Maximize2 className="h-4 w-4" />
          )}
        </Button>
        <span className="ml-2 text-xs text-muted-foreground">
          {Math.round(zoom * 100)}%
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={resetView}
          className="ml-auto text-xs"
        >
          Reset
        </Button>
      </div>

      {/* Image */}
      <div className="flex-1 overflow-auto p-4">
        <div className="flex items-center justify-center min-h-[350px]">
          <img
            src={imageUrl}
            alt="Invoice"
            className="max-w-full transition-transform duration-200"
            style={{
              transform: `scale(${zoom}) rotate(${rotation}deg)`,
              transformOrigin: "center center",
            }}
            draggable={false}
          />
        </div>
      </div>
    </div>
  );
}
