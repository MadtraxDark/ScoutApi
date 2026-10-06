"use client";

export function ProductMatchBanner({
  starting = false,
  recovering = false,
  elapsed,
  storesCompleted,
  storesTotal,
}: {
  starting?: boolean;
  recovering?: boolean;
  elapsed: string;
  storesCompleted?: number;
  storesTotal?: number;
}) {
  const progress =
    storesTotal != null && storesTotal > 0 && storesCompleted != null
      ? `${storesCompleted} de ${storesTotal} lojas concluídas`
      : null;

  return (
    <div className="match-run-banner" role="status" aria-live="polite">
      <span className="match-run-banner-pulse" aria-hidden="true" />
      <div>
        <strong>
          {recovering ? "Aguardando recuperação da busca em outras lojas" : starting
            ? "Solicitando início da busca em outras lojas..."
            : `Busca de produto em outras lojas em andamento: ${elapsed}`}
        </strong>
        {progress ? <p>{progress}</p> : null}
      </div>
    </div>
  );
}
