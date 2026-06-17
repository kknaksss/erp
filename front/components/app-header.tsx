// P1 셸 헤더 골격. 실제 액션/권한 분기는 도메인 phase 에서.
export function AppHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex items-center justify-between border-b border-mgray-100 bg-card px-7 py-4">
      <div>
        <h1 className="text-lg font-semibold text-mgray-800">{title}</h1>
        {description ? (
          <p className="mt-0.5 text-[13px] text-mgray-500">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}
