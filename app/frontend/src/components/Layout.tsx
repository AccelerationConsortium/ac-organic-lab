import { Outlet, NavLink } from 'react-router-dom'
import { LayoutDashboard, Radio, Zap, LineChart, Bell, Target, Database, FlaskConical } from 'lucide-react'
import styles from './Layout.module.css'

const navItems = [
  { path: '/',              label: 'Overview',  icon: LayoutDashboard },
  { path: '/live',          label: 'Live',      icon: Radio },
  { path: '/energy',        label: 'Energy',    icon: Zap },
  { path: '/history',       label: 'History',   icon: LineChart },
  { path: '/alerts',        label: 'Alerts',    icon: Bell },
  { path: '/workflow',      label: 'Workflow',  icon: Target },
  { path: '/platforms/hte', label: 'HTE',       icon: Database },
]

export default function Layout() {
  return (
    <div className={styles.layout}>
      <aside className={styles.sidebar}>
        <div className={styles.logo}>
          <div className={styles.logoIcon}>
            <FlaskConical size={22} strokeWidth={2.5} />
          </div>
          <span className={styles.logoText}>AC Organic Lab</span>
        </div>

        <nav className={styles.nav}>
          {navItems.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) =>
                `${styles.navItem} ${isActive ? styles.navItemActive : ''}`
              }
            >
              <item.icon size={18} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className={styles.sidebarFooter}>
          <div className={styles.systemStatus}>
            <span className={styles.statusDot} />
            <span>System Online</span>
          </div>
        </div>
      </aside>

      <main className={styles.main}>
        <div className={styles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  )
}
