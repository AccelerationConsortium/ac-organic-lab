/** Shapes the API reference reads: JSON Schema fragments as FastAPI emits
 *  them, and the slice of the OpenAPI document the page renders. Shared by the
 *  page and its grouping rules, which is why they are not in either.
 */

export interface JsonSchemaProperty {
  type?: string | string[];
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
  items?: { type?: string };
}

export interface JsonSchema {
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  title?: string;
  description?: string;
}

export interface OpenApiParameter {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
  schema?: JsonSchemaProperty & { $ref?: string };
}

export interface OpenApiOperation {
  summary?: string;
  description?: string;
  tags?: string[];
  parameters?: OpenApiParameter[];
  requestBody?: {
    content?: Record<string, { schema?: JsonSchema & { $ref?: string } }>;
  };
}

export interface OpenApiDoc {
  paths: Record<string, Record<string, OpenApiOperation>>;
  components?: { schemas?: Record<string, JsonSchema> };
}

export interface Endpoint {
  method: string;
  path: string;
  op: OpenApiOperation;
}

/** One tag's endpoints, split by the first path segment that differs. */
export interface SubModule {
  name: string;
  endpoints: Endpoint[];
}

// --- the devices' actions, from GET /api/catalog ---

export interface ActionDef {
  name: string;
  description: string;
  method: string;
  endpoint: string;
  args_schema: JsonSchema;
  requires_states: string[];
  estimated_duration_s: number | null;
}

export interface DocumentationEndpoint {
  label: string;
  kind: "swagger" | "openapi" | "json" | "markdown" | "text";
  source_path: string;
  url: string;
}

export interface InstrumentCatalog {
  id: string;
  name: string;
  kind: string;
  adapter: string;
  base_url: string;
  protocol: string;
  documentation?: DocumentationEndpoint[];
  actions: ActionDef[];
}

export interface PlatformCatalog {
  label: string;
  instruments: InstrumentCatalog[];
}

export interface CatalogResponse {
  platforms: Record<string, PlatformCatalog>;
}
