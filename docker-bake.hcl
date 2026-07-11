// Single published image built from the Dockerfile (~1 GB, no browser).
//
// Local:   docker buildx bake              (build, no push)
//          docker buildx bake --push       (push; needs REGISTRY login)
// CI sets REGISTRY + VERSION; see .github/workflows/docker-publish.yml

variable "REGISTRY" {
  default = "ghcr.io/aroxu/pinchive"
}

variable "VERSION" {
  default = "dev"
}

group "default" {
  targets = ["slim"]
}

target "slim" {
  context    = "."
  dockerfile = "Dockerfile"
  platforms  = ["linux/amd64", "linux/arm64"]
  tags = [
    "${REGISTRY}:latest",
    "${REGISTRY}:${VERSION}",
  ]
}
