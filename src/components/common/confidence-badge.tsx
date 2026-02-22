import { Badge } from "@/components/ui/badge";
import { getConfidenceLevel } from "@/lib/utils";

interface ConfidenceBadgeProps {
  score: number;
  showLabel?: boolean;
  className?: string;
}

export function ConfidenceBadge({
  score,
  showLabel = true,
  className,
}: ConfidenceBadgeProps) {
  const level = getConfidenceLevel(score);

  const variantMap = {
    high: "success" as const,
    medium: "warning" as const,
    low: "destructive" as const,
  };

  const labelMap = {
    high: "High",
    medium: "Medium",
    low: "Low",
  };

  return (
    <Badge variant={variantMap[level]} className={className}>
      {(score * 100).toFixed(0)}%
      {showLabel && (
        <span className="ml-1">({labelMap[level]})</span>
      )}
    </Badge>
  );
}
