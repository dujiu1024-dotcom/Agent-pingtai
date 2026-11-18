import React from 'react'
type Props={children:React.ReactNode}
type State={error:Error|null}
export class ErrorBoundary extends React.Component<Props,State>{
 state:State={error:null}
 static getDerivedStateFromError(error:Error):State{return {error}}
 componentDidCatch(error:Error,info:React.ErrorInfo){console.error('[frontend:error-boundary]',{message:error.message,componentStack:info.componentStack})}
 render(){if(this.state.error)return <main className="fatal-error" role="alert"><strong>页面发生异常</strong><p>页面未能正常显示。可以尝试重新加载。</p><button onClick={()=>window.location.reload()}>重新加载</button></main>;return this.props.children}
}