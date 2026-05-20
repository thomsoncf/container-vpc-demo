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
};

export class MyContainer extends Container<Env> {
  // Port the container app inside the image listens on.
  defaultPort = 8080;
  sleepAfter = "2m";

  // Deny-by-default egress. The container can only reach the virtual
  // hostname we explicitly allow below.
  enableInternet = false;
  allowedHosts = ["acme-products.internal"];
}

// Intercept HTTP calls from inside the container to acme-products.internal
// and forward them through the Workers VPC binding into the cloudflared tunnel.
// This handler runs in the Workers runtime, NOT inside the container sandbox,
// so env.PRIVATE_API is fully available.
MyContainer.outboundByHost = {
  "acme-products.internal": async (request, env, ctx) => {
    console.log(
      `[${ctx.containerId}] container -> VPC: ${request.method} ${request.url}`,
    );
    // The VPC Service config (host=127.0.0.1, port=8080, tunnel=thomson-vm)
    // determines the upstream target. Only path + query + headers are
    // forwarded from the inbound request.
    return env.PRIVATE_API.fetch(request);
  },
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/") {
      return new Response(
        "container-vpc-demo: try /products or /products/sku-001 or /health\n",
        { headers: { "content-type": "text/plain" } },
      );
    }

    // Forward the inbound path/query to the container as-is.
    return getContainer(env.MY_CONTAINER).fetch(request);
  },
} satisfies ExportedHandler<Env>;
