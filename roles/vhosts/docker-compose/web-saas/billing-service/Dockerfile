# Build stage
FROM golang:1.23-alpine AS builder

WORKDIR /app

# Install dependencies (git for go mod, if needed)
RUN apk add --no-cache git

# Copy go mod and sum files
COPY go.mod go.sum ./
RUN go mod download

# Copy the source code
COPY . .

# Build the Go app
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -buildvcs=false -ldflags="-w -s" -o /app/billing-service ./cmd/billing-service

# Final stage
FROM alpine:3.20

WORKDIR /app

# Install ca-certificates and tzdata
RUN apk --no-cache add ca-certificates tzdata

# Copy the binary from the builder stage
COPY --from=builder /app/billing-service .

# Expose the default billing service port
EXPOSE 8081

# Command to run the executable
CMD ["./billing-service"]
