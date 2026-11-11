import type { JSX } from 'react';
import { isHttpUrl } from '../../shared/api/responseValidation.ts';
import { valueBasisText } from './serviceModel.ts';
import type { ServiceValue } from './serviceModel.ts';

export function ServiceValueBasis({ service, virtualData }: {
  service: ServiceValue;
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
