import {
  Container,
  ContainerProxy,
  getContainer,
} from "@cloudflare/containers";

// Required export so the platform can run outbound interception.
export { ContainerProxy };

type Env = {
  MY_CONTAINER: DurableObjectNamespace;
  PRIVATE_API: Fetcher; // Workers VPC Service binding → acme-products
  SLOW_VPC: Fetcher; // Workers VPC Service binding → slow-via-vpc (VM 127.0.0.1:9000)
  MAC_VPC: Fetcher; // Workers VPC Service binding → slow-via-mac (this Mac 127.0.0.1:9000)
};

export class MyContainer extends Container<Env> {
  // Port the container app inside the image listens on.
  defaultPort = 8080;
  sleepAfter = "2m";

  // Container can reach:
  //   acme-products.internal -> VPC binding to products-api (existing demo)
  //   slow-vpc.internal      -> VPC binding to slow-service (TIMEOUT PROBE)
  //   slow.demoflair.com     -> direct HTTPS through Cloudflare edge
  //                             (the proxy_read_timeout cache rule path)
  enableInternet = false;
  allowedHosts = [
    "acme-products.internal",
    "slow-vpc.internal",
    "slow.demoflair.com",
  ];
}

// Intercept HTTP calls from inside the container and forward them through
// the matching Workers VPC binding into the cloudflared tunnel. These
// handlers run in the Workers runtime, NOT inside the container sandbox,
// so env bindings are fully available.
MyContainer.outboundByHost = {
  "acme-products.internal": async (request, env, ctx) => {
    console.log(
      `[${ctx.containerId}] container -> VPC(acme-products): ${request.method} ${request.url}`,
    );
    return env.PRIVATE_API.fetch(request);
  },

  // Probe path: container calls http://slow-vpc.internal/slow?delay=N
  // -> Worker forwards via the SLOW_VPC binding -> tunnel -> 127.0.0.1:9000.
  // NO Cloudflare edge proxy in this path, so this is a pure measurement
  // of the VPC binding / tunnel layer's own timeout.
  "slow-vpc.internal": async (request, env, ctx) => {
    const t0 = Date.now();
    console.log(
      `[${ctx.containerId}] container -> VPC(slow): ${request.method} ${request.url}`,
    );
    try {
      const resp = await env.SLOW_VPC.fetch(request);
      console.log(
        `[${ctx.containerId}] VPC(slow) ok after ${Date.now() - t0}ms status=${resp.status}`,
      );
      return resp;
    } catch (e) {
      const dt = Date.now() - t0;
      const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
      console.log(`[${ctx.containerId}] VPC(slow) error after ${dt}ms: ${msg}`);
      return new Response(
        JSON.stringify({
          vpc_error: true,
          error: msg,
          elapsed_ms: dt,
        }),
        { status: 504, headers: { "content-type": "application/json" } },
      );
    }
  },
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/") {
      return new Response(
        "container-vpc-demo: try /products | /slow?delay=N | /vpc-slow?delay=N | /direct-vpc-slow?delay=N\n",
        { headers: { "content-type": "text/plain" } },
      );
    }

    // Direct VPC binding test (no container in the chain).
    // Isolates the Worker -> VPC binding -> tunnel -> origin path.
    //   /direct-vpc-slow      -> SLOW_VPC -> VM tunnel
    //   /direct-mac-vpc-slow  -> MAC_VPC  -> local Mac tunnel
    const directMatch = url.pathname.match(/^\/direct-(slow|mac)-vpc(?:-slow)?$/);
    if (
      url.pathname.startsWith("/direct-vpc-slow") ||
      url.pathname.startsWith("/direct-mac-vpc-slow")
    ) {
      const isLocal = url.pathname.startsWith("/direct-mac-vpc-slow");
      const binding: Fetcher = isLocal ? env.MAC_VPC : env.SLOW_VPC;
      const routeName = isLocal ? "direct-mac-vpc-binding" : "direct-vpc-binding";
      const delay = url.searchParams.get("delay") ?? "5";
      const nonce = url.searchParams.get("nonce") ?? Date.now().toString();
      const t0 = Date.now();
      try {
        const resp = await binding.fetch(
          `http://slow-service-via-vpc/slow?delay=${delay}&nonce=${nonce}`,
        );
        const body = await resp.text();
        const dt = Date.now() - t0;
        return new Response(
          JSON.stringify({
            route: routeName,
            elapsed_ms: dt,
            upstream_status: resp.status,
            upstream_x_served_by: resp.headers.get("x-served-by"),
            upstream_body: tryJSON(body),
          }, null, 2),
          { headers: { "content-type": "application/json" } },
        );
      } catch (e) {
        const dt = Date.now() - t0;
        const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
        return new Response(
          JSON.stringify({
            route: routeName,
            error: msg,
            elapsed_ms: dt,
          }, null, 2),
          { status: 504, headers: { "content-type": "application/json" } },
        );
      }
    }

    // Forward the inbound path/query to the container as-is.
    return getContainer(env.MY_CONTAINER).fetch(request);
  },
} satisfies ExportedHandler<Env>;

function tryJSON(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}
