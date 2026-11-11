import type { JSX } from 'react';
import { isHttpUrl } from '../../shared/api/responseValidation.ts';
import { valueBasisText } from './serviceModel.ts';
import type { InKindService } from '../../shared/api/generated/service.ts';

export function ServiceValueBasis({ service, virtualData }: {
  service: InKindService;
  virtualData: boolean;
}): JSX.Element {
  return (
    <>
      {valueBasisText(service)}
      {!virtualData && service.value_source_url !== null && isHttpUrl(service.value_source_url) ? (
        <> · <a href={service.value_source_url} target="_blank" rel="noopener noreferrer">단가 출처 ↗</a></>
      ) : null}
    </>
  );
}
