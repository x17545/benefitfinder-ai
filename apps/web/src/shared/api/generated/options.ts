/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */

/**
 * @minItems 1
 */
export type BandList = [
  {
    value: string;
    label: string;
  },
  ...{
    value: string;
    label: string;
  }[]
];

/**
 * GET /api/v1/options 응답. *_band value는 youth-profile.schema.json의 열거값과 일치한다.
 */
export interface SelectOptions {
  regions: {
    sido_code: string;
    sido_name: string;
    sigungu: {
      code: string;
      name: string;
    }[];
  }[];
  bands: {
    income: BandList;
    asset: BandList;
    deposit: BandList;
    rent: BandList;
    parent_income: BandList;
    parent_asset: BandList;
  };
}
