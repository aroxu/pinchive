// Two published image variants from the one Dockerfile:
//   slim        -> default, no browser (INSTALL_PLAYWRIGHT=false)   ~1 GB
//   playwright  -> bundles chromium for the re-login fallback        ~1.4 GB
//
// Local:   docker buildx bake                 (builds both, no push)
//          docker buildx bake slim            (just the slim one)
//          docker buildx bake --push          (push both; needs REGISTRY login)
// CI sets REGISTRY + VERSION; see .github/workflows/docker-publish.yml

variable "REGISTRY" {
  default = "ghcr.io/aroxu/pinchive"
}

variable "VERSION" {
  default = "dev"
}

group "default" {
  targets = ["slim", "playwright"]
}

target "_common" {
  context    = "."
  dockerfile = "Dockerfile"
  platforms  = ["linux/amd64"]
}

target "slim" {
  inherits = ["_common"]
  args = {
    INSTALL_PLAYWRIGHT = "false"
  }
  tags = [
    "${REGISTRY}:latest",
    "${REGISTRY}:${VERSION}",
  ]
}

target "playwright" {
  inherits = ["_common"]
  args = {
    INSTALL_PLAYWRIGHT = "true"
  }
  tags = [
    "${REGISTRY}:playwright",
    "${REGISTRY}:${VERSION}-playwright",
  ]
}
