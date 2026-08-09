import React from 'react'

export function SkeletonLine({ width = '100%', height = '14px', mb = '8px' }) {
  return (
    <div
      className="skeleton"
      style={{ width, height, marginBottom: mb, borderRadius: '4px' }}
    />
  )
}

export function SkeletonCard({ children }) {
  return (
    <div className="card" style={{ padding: '20px', marginBottom: '12px' }}>
      {children}
    </div>
  )
}

export function ContractSkeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div className="card" style={{ padding: '24px' }}>
        <SkeletonLine width="60%" height="28px" mb="12px" />
        <SkeletonLine width="80%" height="16px" mb="8px" />
        <SkeletonLine width="45%" height="16px" mb="0" />
      </div>
      {[1,2,3,4].map(i => (
        <div className="card" style={{ padding: '20px' }} key={i}>
          <SkeletonLine width="40%" height="18px" mb="16px" />
          <SkeletonLine width="100%" height="13px" mb="8px" />
          <SkeletonLine width="85%" height="13px" mb="8px" />
          <SkeletonLine width="70%" height="13px" mb="0" />
        </div>
      ))}
    </div>
  )
}

export function QueueSkeleton() {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: '16px' }}>
      {[1,2,3].map(i => (
        <div className="card" style={{ padding: '20px', borderRadius: '10px' }} key={i}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '16px' }}>
            <SkeletonLine width="80px" height="20px" mb="0" />
            <SkeletonLine width="60px" height="20px" mb="0" />
          </div>
          <SkeletonLine width="40%" height="28px" mb="6px" />
          <SkeletonLine width="70%" height="16px" mb="16px" />
          <div style={{ display: 'flex', gap: '8px' }}>
            <SkeletonLine width="90px" height="48px" mb="0" />
            <SkeletonLine width="90px" height="48px" mb="0" />
            <SkeletonLine width="90px" height="48px" mb="0" />
          </div>
        </div>
      ))}
    </div>
  )
}
