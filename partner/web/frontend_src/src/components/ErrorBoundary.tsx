import React from "react";

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { err: Error | null }
> {
  state = { err: null as Error | null };

  static getDerivedStateFromError(err: Error) {
    return { err };
  }

  componentDidCatch(err: Error) {
    console.error("ErrorBoundary caught:", err);
  }

  render() {
    if (this.state.err) {
      return (
        <div className="error-boundary">
          <h2>页面渲染失败</h2>
          <pre>{this.state.err.message}</pre>
          <button onClick={() => this.setState({ err: null })}>重试</button>
        </div>
      );
    }
    return this.props.children;
  }
}
