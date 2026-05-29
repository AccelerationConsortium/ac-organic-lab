import type { ReactNode } from 'react'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import styles from './MetricCard.module.css'

interface MetricCardProps {
  label: string
  value: ReactNode
  sublabel?: string
  trend?: 'up' | 'down' | 'neutral'
  trendValue?: string
  icon?: ReactNode
  variant?: 'default' | 'accent' | 'success' | 'warning' | 'error'
  size?: 'sm' | 'md' | 'lg'
  labelColor?: string
}

export default function MetricCard({
  label,
  value,
  sublabel,
  trend,
  trendValue,
  icon,
  variant = 'default',
  size = 'md',
  labelColor,
}: MetricCardProps) {
  const TrendIcon = trend === 'up' ? TrendingUp : trend === 'down' ? TrendingDown : Minus

  return (
    <div className={`${styles.card} ${styles[variant]} ${styles[size]}`}>
      <div className={styles.header}>
        <span className={styles.label} style={labelColor ? { color: labelColor } : undefined}>{label}</span>
        {icon && <div className={styles.icon} style={labelColor ? { color: labelColor } : undefined}>{icon}</div>}
      </div>

      <div className={styles.valueRow}>
        <span className={styles.value}>{value}</span>
        {trend && (
          <span className={`${styles.trend} ${styles[`trend-${trend}`]}`}>
            <TrendIcon size={14} />
            {trendValue && <span>{trendValue}</span>}
          </span>
        )}
      </div>

      {sublabel && <span className={styles.sublabel}>{sublabel}</span>}
    </div>
  )
}
